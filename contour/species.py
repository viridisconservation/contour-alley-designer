"""Species gate (NF4). Rejects invasive-listed and non-fixing taxa used as fixers by default."""
import json
from pathlib import Path

ROLES = {'tree_crop', 'fixer_tree', 'cover_legume', 'timber'}
FIXATION = {'rhizobial', 'actinorhizal', 'none'}
INVASIVE = {'not_listed', 'listed', 'listed_some_jurisdictions', 'unknown'}
REQUIRED = {'id', 'taxon', 'common', 'roles', 'fixation', 'native_region', 'invasive_status', 'notes', 'source'}


def load(path):
    data = json.loads(Path(path).read_text())
    if data.get('schema') != 'ViridisSpecies-1':
        raise ValueError('unsupported species schema')
    table = {}
    for s in data['species']:
        if set(s) != REQUIRED:
            raise ValueError(f"species entry {s.get('id')} must have exactly {sorted(REQUIRED)}")
        if not set(s['roles']) <= ROLES or s['fixation'] not in FIXATION or s['invasive_status'] not in INVASIVE:
            raise ValueError(f"invalid role/fixation/invasive status for {s['id']}")
        table[s['id']] = s
    return {'region': data['region'], 'status': data['status'], 'species': table}


def gate(table, management, overrides=None):
    """Return (accepted, warnings). Raises on a blocking violation."""
    overrides = overrides or {}
    warnings = []
    picks = [('tree_crop', i) for i in management.get('tree_crops', [])]
    picks += [('fixer_tree', i) for i in management.get('fixer_trees', [])]
    picks += [('cover_legume', i) for i in management.get('cover_legumes', [])]
    for role, sid in picks:
        if sid not in table['species']:
            raise ValueError(f'species {sid} not declared in species table')
        s = table['species'][sid]
        if role not in s['roles']:
            raise ValueError(f"{s['taxon']} is not declared for role {role}")
        if role in ('fixer_tree', 'cover_legume') and s['fixation'] == 'none':
            raise ValueError(f"{s['taxon']} does not fix nitrogen and cannot be used as a {role}")
        if s['invasive_status'] in ('listed', 'listed_some_jurisdictions'):
            reason = overrides.get(sid)
            if not reason:
                raise ValueError(f"{s['taxon']} is invasive-listed ({s['invasive_status']}); "
                                 'an override with written justification is required')
            warnings.append(f"OVERRIDE: {s['taxon']} used despite invasive listing: {reason}")
        if s['invasive_status'] == 'unknown':
            warnings.append(f"{s['taxon']}: invasive status unknown for {table['region']}; verify before use")
    if table['status'] != 'verified':
        warnings.append(f"species table status is '{table['status']}': statuses must be verified against "
                        'current state/provincial invasive lists before field use')
    return [sid for _, sid in picks], warnings
