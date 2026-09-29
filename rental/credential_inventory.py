"""Aggregate-only legacy credential inventory. No password values leave MongoDB."""
from datetime import datetime, timezone

COUNTS = ('accounts', 'stored_password_accounts', 'active_stored_password_accounts', 'malformed_fields')
GROUPS = ('owners', 'investors', 'other')


def credential_inventory_pipeline():
    field_type = {'$type': '$_temp_password'}
    return [
        {'$project': {
            '_id': 0,
            'account_group': {'$switch': {
                'branches': [
                    {'case': {'$in': ['$role', ['owner', 'landlord']]}, 'then': 'owners'},
                    {'case': {'$eq': ['$role', 'investor']}, 'then': 'investors'},
                ],
                'default': 'other',
            }},
            'has_stored_password': {'$cond': [
                {'$eq': [field_type, 'string']},
                {'$gt': [{'$strLenCP': '$_temp_password'}, 0]},
                False,
            ]},
            'malformed_field': {'$not': [{'$in': [field_type, ['missing', 'null', 'string']]}]},
            'active_account': {'$and': [
                {'$ne': [{'$ifNull': ['$deleted', False]}, True]},
                {'$eq': [{'$ifNull': ['$status', 'active']}, 'active']},
            ]},
        }},
        {'$group': {
            '_id': '$account_group',
            'accounts': {'$sum': 1},
            'stored_password_accounts': {'$sum': {'$cond': ['$has_stored_password', 1, 0]}},
            'active_stored_password_accounts': {'$sum': {'$cond': [
                {'$and': ['$has_stored_password', '$active_account']}, 1, 0,
            ]}},
            'malformed_fields': {'$sum': {'$cond': ['$malformed_field', 1, 0]}},
        }},
    ]


async def legacy_credential_summary(db):
    rows = await db.app_users.aggregate(
        credential_inventory_pipeline(), maxTimeMS=5000, allowDiskUse=False,
    ).to_list(length=4)
    if not isinstance(rows, list) or len(rows) > len(GROUPS):
        raise ValueError('invalid_credential_summary')
    groups = {name: {key: 0 for key in COUNTS} for name in GROUPS}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('_id') not in GROUPS or row['_id'] in seen:
            raise ValueError('invalid_credential_summary')
        counts = {key: row.get(key) for key in COUNTS}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError('invalid_credential_summary')
        if (counts['active_stored_password_accounts'] > counts['stored_password_accounts'] or
                counts['stored_password_accounts'] + counts['malformed_fields'] > counts['accounts']):
            raise ValueError('invalid_credential_summary')
        seen.add(row['_id'])
        groups[row['_id']] = counts
    totals = {key: sum(group[key] for group in groups.values()) for key in COUNTS}
    return {
        'success': True,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'app_users',
        'groups': groups,
        'totals': totals,
        'requires_review': totals['stored_password_accounts'] > 0 or totals['malformed_fields'] > 0,
        'password_strength_assessed': False,
        'credential_rotation_performed': False,
    }
