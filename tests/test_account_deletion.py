import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from bson import ObjectId
from fastapi import HTTPException
from rental.account_deletion import close_app_account
from rental import shared


class Collection:
    def __init__(self, rows=()):
        self.rows = copy.deepcopy(list(rows))
        self.fail = False
        self.no_match = False

    def matches(self, row, query):
        return all(row.get(k) == v for k, v in query.items())

    async def find_one(self, query):
        return next((copy.deepcopy(r) for r in self.rows if self.matches(r, query)), None)

    async def update_one(self, query, change, upsert=False):
        if self.fail:
            raise RuntimeError('private database error')
        if self.no_match:
            return SimpleNamespace(matched_count=0)
        row = next((r for r in self.rows if self.matches(r, query)), None)
        if row is None and upsert:
            row = {**query, **change.get('$setOnInsert', {})}
            self.rows.append(row)
        if row is None:
            return SimpleNamespace(matched_count=0)
        row.update(change.get('$set', {}))
        for k in change.get('$unset', {}):
            row.pop(k, None)
        return SimpleNamespace(matched_count=1)

    async def update_many(self, query, change):
        if self.fail:
            raise RuntimeError('private database error')
        for r in self.rows:
            if self.matches(r, query):
                r.update(change['$set'])

    async def delete_many(self, query):
        if self.fail:
            raise RuntimeError('private database error')
        self.rows = [r for r in self.rows if not self.matches(r, query)]


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.oid = ObjectId()
        self.uid = str(self.oid)
        self.user = {'_id': self.oid, 'role': 'tenant', 'name': 'Fixture', 'email': 'fixture@example.test', 'status': 'active', 'push_token': 'test-only'}
        self.db = SimpleNamespace(
            app_users=Collection([self.user]), tenants=Collection(),
            deleted_accounts=Collection(),
            autopay_config=Collection([{'user_id': self.uid}, {'user_id': 'other'}]),
            chat_messages=Collection([{'sender_id': self.uid, 'body': 'retained'}, {'sender_id': 'other', 'sender_name': 'Other'}]),
            auth_sessions=Collection([{'user_id': self.uid, 'revoked_at': None}, {'user_id': 'other', 'revoked_at': None}]),
        )

    def close(self):
        return asyncio.run(close_app_account(self.db, self.user))

    def test_success_is_scoped_and_does_not_claim_full_erasure(self):
        result = self.close()
        self.assertTrue(result['account_closed'])
        self.assertFalse(result['data_erasure_complete'])
        self.assertEqual(self.db.app_users.rows[0]['status'], 'deleted')
        self.assertNotIn('push_token', self.db.app_users.rows[0])
        self.assertIsNotNone(self.db.auth_sessions.rows[0]['revoked_at'])
        self.assertIsNone(self.db.auth_sessions.rows[1]['revoked_at'])
        self.assertEqual(self.db.autopay_config.rows, [{'user_id': 'other'}])
        self.assertEqual(self.db.chat_messages.rows[0]['body'], 'retained')
        self.assertEqual(self.db.chat_messages.rows[1]['sender_name'], 'Other')
        self.assertEqual(self.db.deleted_accounts.rows[0]['state'], 'completed')

    def test_tenant_fallback_without_app_profile_has_no_writes(self):
        self.db.app_users.rows = []
        with self.assertRaises(HTTPException) as e:
            self.close()
        self.assertEqual(e.exception.status_code, 409)
        self.assertEqual(self.db.deleted_accounts.rows, [])
        self.assertEqual(len(self.db.autopay_config.rows), 2)

    def test_admin_is_not_self_deleted(self):
        self.db.app_users.rows[0]['role'] = 'admin'
        with self.assertRaises(HTTPException) as e:
            self.close()
        self.assertEqual(e.exception.status_code, 403)
        self.assertEqual(self.db.deleted_accounts.rows, [])

    def test_missing_update_does_not_return_success(self):
        self.db.app_users.no_match = True
        with self.assertRaises(HTTPException) as e:
            self.close()
        self.assertEqual(e.exception.status_code, 503)
        self.assertEqual(self.db.deleted_accounts.rows[0]['failed_step'], 'close_profile')

    def test_session_failure_is_visible_and_retry_preserves_one_archive(self):
        self.db.auth_sessions.fail = True
        with self.assertRaises(HTTPException) as e:
            self.close()
        self.assertEqual(e.exception.detail['code'], 'account_deletion_incomplete')
        self.assertNotIn('private database error', str(e.exception.detail))
        self.assertEqual(self.db.app_users.rows[0]['status'], 'active')
        self.assertEqual(self.db.deleted_accounts.rows[0]['state'], 'incomplete')
        self.db.auth_sessions.fail = False
        self.close()
        self.assertEqual(len(self.db.deleted_accounts.rows), 1)
        self.assertEqual(self.db.deleted_accounts.rows[0]['email'], 'fixture@example.test')
        self.assertEqual(self.db.deleted_accounts.rows[0]['state'], 'completed')

    def test_deleted_profile_rejects_even_an_otherwise_valid_token(self):
        self.close()
        token = jwt.encode({'type': 'marketplace', 'user_id': self.uid}, shared.TENANT_JWT_SECRET, algorithm='HS256')
        request = SimpleNamespace(headers={'Authorization': 'Bearer ' + token})
        with patch.object(shared, 'get_db', return_value=self.db), patch.object(shared, '_validate_session_claims', new=AsyncMock()):
            with self.assertRaises(HTTPException) as e:
                asyncio.run(shared.auth_marketplace(request))
        self.assertEqual(e.exception.status_code, 401)
        self.assertEqual(e.exception.detail, 'account_deleted')


if __name__ == '__main__':
    unittest.main()
