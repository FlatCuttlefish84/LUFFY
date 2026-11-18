import os
import re
import subprocess
import sys
from collections import OrderedDict

README = 'README.md'
TODO_HEADING = '### \U0001f4dd Complete TODO List'
ITEM_RE = re.compile(r'^- \[ \] \*\*(?P<path>.+):(?P<line>\d+)\*\* - (?P<msg>.*)$')

PUSH = os.environ.get('INPUT_PUSH', 'false').strip().lower() == 'true'
BASE = os.environ.get('INPUT_BASE', 'origin/main').strip() or 'origin/main'


def run(cmd, check=True):
    print('$ ' + cmd)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if p.stdout:
        print(p.stdout)
    if p.stderr:
        print(p.stderr, file=sys.stderr)
    if check and p.returncode != 0:
        raise SystemExit(f'command failed: {cmd}\n{p.stderr}')
    return p.returncode, p.stdout


def changed_py_files():
    rc, out = run(f'git diff --name-only {BASE}...HEAD', check=False)
    files = [l for l in out.splitlines() if l.endswith('.py')]
    if not files:
        _, out = run(f'git diff --name-only {BASE} HEAD')
        files = [l for l in out.splitlines() if l.endswith('.py')]
    return list(dict.fromkeys(files))


def all_py_files():
    found = []
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in ('.git', '.github', '__pycache__', '.venv', 'venv')]
        for f in files:
            if f.endswith('.py'):
                p = os.path.relpath(os.path.join(root, f), '.').replace('\\', '/')
                if not p.startswith('.github/'):
                    found.append(p)
    return sorted(found)


def extract_todos(path):
    if not os.path.exists(path):
        return None
    items = []
    with open(path, encoding='utf-8') as fh:
        for idx, raw in enumerate(fh, 1):
            line = raw.rstrip('\n').rstrip('\r')
            hashpos = line.find('#')
            if hashpos < 0:
                continue
            comment = line[hashpos:]
            if not re.search(r'\bTODO\b', comment):
                continue
            body = comment[1:]
            body = body.strip()
            assert body.startswith('TODO'), (path, idx, body)
            body = body[len('TODO'):]
            body = body.lstrip()
            if body.startswith('('):
                m = re.match(r'^\(([^)]*)\)(.*)$', body)
                author = m.group(1)
                rest = m.group(2)
                had_colon = bool(rest.lstrip().startswith(':'))
                rest = rest.lstrip()
                if rest.startswith(':'):
                    rest = rest[1:].lstrip()
                msg = rest.strip()
                rendered = f'({author})' + (':' if had_colon else '')
                if msg:
                    rendered += ' ' + msg
            else:
                t = body
                if t.startswith(':'):
                    t = t[1:]
                rendered = t.strip()
            items.append((idx, rendered))
    items.sort(key=lambda x: x[0])
    return items


def main():
    changed = changed_py_files()
    print('Changed .py files introduced by the main-to-dev diff:')
    for f in changed:
        print('  -', f)

    pyfiles = all_py_files()
    print(f'Searched all {len(pyfiles)} .py files in the repository for TODOs.')
    for f in pyfiles:
        print('    *', f)

    if not os.path.exists(README):
        raise SystemExit(f'{README} not found')

    original = open(README, encoding='utf-8', newline='').read()
    lines = original.split('\n')

    h = None
    for i, l in enumerate(lines):
        if l.rstrip('\r') == TODO_HEADING:
            h = i
            break
    if h is None:
        raise SystemExit(f'Heading not found: {TODO_HEADING!r}')

    e = None
    for i in range(h + 1, len(lines)):
        if re.match(r'^## ', lines[i]):
            e = i
            break
    if e is None:
        e = len(lines)

    first = None
    last = None
    for j in range(h + 1, e):
        if ITEM_RE.match(lines[j]):
            if first is None:
                first = j
            last = j

    if first is None:
        raise SystemExit('No TODO items found in section; refusing to modify.')

    ordered_paths = []
    groups = OrderedDict()
    for j in range(first, last + 1):
        m = ITEM_RE.match(lines[j])
        if not m:
            print('NOTE non-item line in TODO block:', repr(lines[j]))
            continue
        p, ln, msg = m.group('path'), int(m.group('line')), m.group('msg')
        if p not in groups:
            groups[p] = []
            ordered_paths.append(p)
        groups[p].append((ln, msg))

    for p in groups:
        lns = [x[0] for x in groups[p]]
        assert lns == sorted(lns), f'baseline lines not increasing for {p}'
    baseline_paths = list(groups.keys())
    assert baseline_paths == sorted(baseline_paths), 'baseline file order is not lexicographic'

    for p in changed:
        its = extract_todos(p)
        if its is None:
            if p in groups:
                del groups[p]
                ordered_paths.remove(p)
                print(f'REMOVE group (file deleted): {p}')
            continue
        if p in groups:
            old = groups[p]
            old_map = dict(old)
            new_map = dict(its)
            groups[p] = its
            print(f'UPDATE {p}: {len(old)} -> {len(its)} items')
            for ln, msg in its:
                if ln not in old_map:
                    print(f'  + {p}:{ln} - {msg}  [ADDED]')
            for ln, msg in old:
                if ln not in new_map:
                    print(f'  - {p}:{ln} - {msg}  [REMOVED]')
            for ln, msg in its:
                if ln in old_map and old_map[ln] == msg:
                    print(f'  = {p}:{ln} message unchanged')
        else:
            groups[p] = its
            pos = 0
            while pos < len(ordered_paths) and ordered_paths[pos] < p:
                pos += 1
            ordered_paths.insert(pos, p)
            print(f'ADD group: {p} ({len(its)} items)')

    new_item_lines = []
    for p in ordered_paths:
        its = sorted(groups[p], key=lambda x: x[0])
        for ln, msg in its:
            new_item_lines.append(f'- [ ] **{p}:{ln}** - {msg}')

    new_lines = lines[:first] + new_item_lines + lines[last + 1:]
    updated = '\n'.join(new_lines)

    assert updated != original or not changed, 'Expected README to change for changed .py files'
    assert TODO_HEADING in updated
    old_nonitems = [l for l in lines if not ITEM_RE.match(l)]
    new_nonitems = [l for l in new_lines if not ITEM_RE.match(l)]
    assert old_nonitems == new_nonitems, 'Non-item content of README was modified!'

    final = OrderedDict()
    for l in new_lines:
        m = ITEM_RE.match(l)
        if m:
            final.setdefault(m.group('path'), []).append(int(m.group('line')))
    fps = list(final.keys())
    assert fps == sorted(fps), 'result file order is not lexicographic'
    for p, lns in final.items():
        assert lns == sorted(lns), f'lines not increasing for {p}'

    with open(README, 'w', encoding='utf-8', newline='') as fh:
        fh.write(updated)

    print('\n==================== README DIFF ====================')
    run('git --no-pager diff -- README.md')
    print('==================== DIFF STAT ====================')
    run('git --no-pager diff --stat -- README.md')

    if PUSH:
        if not changed:
            print('No .py changes in main-to-dev diff; nothing to push.')
            return
        _, status = run('git status --porcelain')
        if not status.strip():
            print('No changes to commit; nothing to push.')
            return
        run(f'git add {README}')
        run('git -c user.name=github-actions[bot] -c user.email=41898282+github-actions[bot]@users.noreply.github.com '
            'commit -m "docs: update Complete TODO List for main-to-dev changes\n\n'
            '- Remove TODO items completed on the dev branch\n'
            '- Add newly introduced TODO items\n'
            '- Refresh path/line numbers while keeping the existing README format"')
        run('git push origin HEAD:dev')
        print('Pushed to dev successfully.')
    else:
        print('DRY RUN only (INPUT_PUSH=false). No commit/push performed.')


if __name__ == '__main__':
    main()
