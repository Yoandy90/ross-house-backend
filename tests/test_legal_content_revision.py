import ast
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('legal_revision_under_test', ROOT / 'rental/legal_content_revision.py')
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


class Collection:
    def __init__(self, doc, concurrent=False):
        self.doc = copy.deepcopy(doc)
        self.markers = {}
        self.concurrent = concurrent

    async def update_one(self, query, update):
        field = next(k for k in query if k != '_id' and not k.startswith('content_revisions.'))
        marker = next(k for k in query if k.startswith('content_revisions.'))
        if self.concurrent:
            self.doc[field] = 'Edited concurrently by administrator'
        if self.doc.get(field) != query[field] or marker in self.markers:
            return types.SimpleNamespace(modified_count=0)
        changes = update['$set']
        self.doc[field] = changes[field]
        self.markers[marker] = changes[marker]
        return types.SimpleNamespace(modified_count=1)


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.router = types.ModuleType('rental.legal_router')
        for node in ast.parse((ROOT / 'rental/legal_router.py').read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        setattr(self.router, target.id, node.value.value)
        self.doc = {'_id': 'legal_config', 'privacy_es': 'Known old content', 'terms_es': 'Custom terms'}
        digest = revision.hashlib.sha256(b'Known old content').hexdigest()
        self.hashes = patch.object(revision, 'LEGACY_HASHES', {'privacy_es': digest})
        self.hashes.start()
        self.addCleanup(self.hashes.stop)

    def run_revision(self, collection, doc):
        with patch.dict(sys.modules, {'rental.legal_router': self.router}):
            return asyncio.run(revision.revise_known_seed_documents(collection, doc))

    def test_known_seed_revised_with_backup_and_repeat_is_noop(self):
        collection = Collection(self.doc)
        self.assertEqual(self.run_revision(collection, self.doc), ['privacy_es'])
        self.assertEqual(collection.doc['privacy_es'], self.router.DEFAULT_PRIVACY_ES.strip())
        self.assertEqual(collection.doc['terms_es'], 'Custom terms')
        self.assertEqual(next(iter(collection.markers.values()))['previous_content'], 'Known old content')
        self.assertEqual(self.run_revision(collection, self.doc), [])
        self.assertEqual(self.run_revision(collection, collection.doc), [])

    def test_custom_or_missing_content_is_not_overwritten(self):
        for value in ['Custom policy', '', None]:
            doc = dict(self.doc, privacy_es=value)
            collection = Collection(doc)
            self.assertEqual(self.run_revision(collection, doc), [])
            self.assertEqual(collection.doc, doc)

    def test_concurrent_admin_edit_wins(self):
        collection = Collection(self.doc, concurrent=True)
        self.assertEqual(self.run_revision(collection, self.doc), [])
        self.assertEqual(collection.doc['privacy_es'], 'Edited concurrently by administrator')
        self.assertEqual(collection.markers, {})

    def test_terms_and_deletion_revise_independently_of_privacy(self):
        fields = ['terms_es', 'terms_en', 'account_deletion_es', 'account_deletion_en']
        digest = revision.hashlib.sha256(b'Known old content').hexdigest()
        doc = dict(self.doc, **{field: 'Known old content' for field in fields})
        # A custom translation must survive while other recognized documents migrate.
        doc['terms_en'] = 'Terms edited by the owner'
        collection = Collection(doc)
        with patch.object(revision, 'LEGACY_HASHES', {field: digest for field in fields}):
            changed = self.run_revision(collection, doc)
        self.assertEqual(changed, ['terms_es', 'account_deletion_es', 'account_deletion_en'])
        self.assertEqual(collection.doc['privacy_es'], doc['privacy_es'])
        self.assertEqual(collection.doc['terms_en'], 'Terms edited by the owner')
        for field in changed:
            self.assertEqual(collection.doc[field], getattr(self.router, 'DEFAULT_' + field.upper()).strip())
        self.assertEqual(len(collection.markers), 3)


if __name__ == '__main__':
    unittest.main()
