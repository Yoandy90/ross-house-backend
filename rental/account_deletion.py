"""Verified account closure; not complete erasure of retained business records."""
import logging
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def close_app_account(db, authenticated_user):
    """Scope every write to the authenticated app account and expose partial failure.

    Operations are repeatable. A pending journal remains if interrupted; support
    can inspect it without mistaking partial progress for completed erasure.
    """
    try:
        oid = ObjectId(str(authenticated_user['_id']))
    except (KeyError, ValueError, TypeError, InvalidId):
        raise HTTPException(409, 'account_deletion_requires_review')
    user_id = str(oid)
    account = await db.app_users.find_one({'_id': oid})
    if not account:
        # auth_marketplace can fall back to tenants; that is not an app_users row.
        raise HTTPException(409, 'account_deletion_requires_review')
    if account.get('role') == 'admin' or authenticated_user.get('role') == 'admin':
        raise HTTPException(403, 'admin_account_deletion_requires_review')

    now = datetime.now(timezone.utc)
    journal_id = 'account_closure:' + user_id
    step = 'record_request'
    try:
        await db.deleted_accounts.update_one(
            {'_id': journal_id},
            {'$setOnInsert': {'user_id': user_id, 'email': account.get('email', ''),
                              'name': account.get('name', ''), 'role': account.get('role', ''),
                              'requested_at': now, 'reason': 'user_requested'},
             '$set': {'state': 'pending', 'updated_at': now}}, upsert=True)
        step = 'remove_autopay'
        await db.autopay_config.delete_many({'user_id': user_id})
        step = 'rename_chat_sender'
        await db.chat_messages.update_many(
            {'sender_id': user_id}, {'$set': {'sender_name': 'Usuario Eliminado'}})
        step = 'revoke_sessions'
        await db.auth_sessions.update_many(
            {'user_id': user_id, 'revoked_at': None},
            {'$set': {'revoked_at': now, 'revoked_reason': 'account_deleted'}})
        step = 'close_profile'
        result = await db.app_users.update_one(
            {'_id': oid},
            {'$set': {'name': 'Cuenta Eliminada', 'email': f'deleted_{user_id}@removed.local',
                      'phone': '', 'status': 'deleted', 'deleted_at': now},
             '$unset': {'push_token': 1, 'push_platform': 1}})
        if result.matched_count != 1:
            raise RuntimeError('account_profile_not_matched')
        step = 'complete_request'
        await db.deleted_accounts.update_one(
            {'_id': journal_id},
            {'$set': {'state': 'completed', 'completed_at': now}, '$unset': {'failed_step': 1}})
    except Exception:
        # Do not log email, tokens or database exception text.
        logger.error('Account closure incomplete at step %s', step)
        try:
            await db.deleted_accounts.update_one(
                {'_id': journal_id}, {'$set': {'state': 'incomplete', 'failed_step': step}})
        except Exception:
            logger.error('Could not record incomplete account closure')
        raise HTTPException(503, {
            'code': 'account_deletion_incomplete',
            'message': 'No se pudo completar el cierre. Contacta a info@rosshouserentals.com. Algunos cambios pueden haberse aplicado.',
        }) from None
    return {
        'success': True, 'account_closed': True, 'data_erasure_complete': False,
        'message': 'Se cerró el perfil de la cuenta y se revocaron sus sesiones. Algunos registros se conservan; consulta la política de eliminación de datos.',
    }
