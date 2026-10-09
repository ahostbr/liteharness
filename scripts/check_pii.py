#!/usr/bin/env python
"""Public shipping guard: hashed private identifiers and machine paths block; names are a printed note.

Legal attribution is permitted by exact file + field + reason. Stable command/API
identifiers have separate compatibility exceptions; neither exempts operational
actor defaults. Scans index bytes in staged mode, tracked worktree bytes in --all.
Binary byte scans are not OCR or compressed-metadata certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Hashes keep the detector itself from republishing sensitive denylist material.
DENIED = {
    '2b7847b7b705781d7cf21a05e9c1bb37cbf078aea103bc3edcc6aca52ab65453': 'private orchestrator identity',
    'e0e3a2b6471d044a53a7757994b51dd33c6b3ec90e1aca21cebc8e2ae79d6d9b': 'private human identity',
    '8566bf36a410f40ad194a0814338eeec6f0db88ada2cc8fd9ae7c80e22e3496a': 'private family identity',
    'f4293461e288327cbf4c3ee1772c2c7e138bf5531bfb5d36ce35ed69a0c85b46': 'private family identity',
    'c81c698ef22c56c8e7ea449d32c7c29035e3ec98e89e80328d65de7368af74a2': 'private author handle outside repository URL',
    '2a1fafaa30cb3a143fa535d5303ed3c3f54892193d6ba87d2d914e706a986e1f': 'private codename',
    '37f6bddc5f0b52f2cee65666ee032a1bb7f56f3edf9e32b264024248939bbc9f': 'private codename',
    'c6b6f3a7c04332b8abbe6f60244f7c50de433d253fae241ca42f4ec5a93dd77e': 'personal email',
    '5c91ccd0905dac433a3fdbb29d8cc28d343ea46e8a05728a9f266fb2a1d4433a': 'personal email',
    '9c90049b1de0a64d3a6409106783e088035da1a06a51426abcccf36555ac09fb': 'private numeric identifier',
    'd8d06e03342377d4739d9ead355d566bda7b1935de149d26336413b927e9de9a': 'private numeric identifier',
}
NOTES = {'private orchestrator identity', 'private human identity'}
# Narrow legal ownership fields, not an author-token exemption.
LEGAL_FIELDS = {
    '.claude-plugin/plugin.json': {'author.name': 'plugin legal author'},
    '.codex-plugin/plugin.json': {'author.name': 'plugin legal author', 'interface.developerName': 'developer attribution'},
    '.claude-plugin/marketplace.json': {'owner.name': 'marketplace owner', 'plugins.0.author.name': 'plugin author'},
}
LEGAL_AUTHOR = '2de03a2c94934f51d7d3a4925b8ecbf2b7d7b7a2b9f0ad3088e3afcbbff279e8'
# Compatibility strings are identifiers, never a default seat/human identity.
COMPATIBILITY = {
    'ls-sentinel': 'stable published skill slug',
    '/v1/sentinel/assistant-message': 'stable bridge API route',
    'skills/sentinel/': 'private skill exclusion',
    'prompts/cognitive-architectures/orchestrator/sentinel.md': 'private architecture exclusion',
}
_USER_SEGMENT = 'Users'
HOME_RE = re.compile(r'(?:[A-Za-z]:[\\/]' + _USER_SEGMENT + r'[\\/]|/c/' + _USER_SEGMENT + r'/)([^\\/\s\"\'<>]+)', re.I)
PLACEHOLDERS = {'testuser', 'user', 'username', 'youruser', 'you', 'example', 'name', 'me', 'yourname', 'public', 'default'}
PATH_PATTERNS = [
    ('private drive', re.compile(r'E:[\\/]SAS', re.I)),
    ('machine project root', re.compile(r'C:[\\/]Projects(?:[\\/]|\b)', re.I)),
]
WORD_RE = re.compile(r'[A-Za-z0-9]+')
REPO_URL = re.compile(r'https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+(?![A-Za-z0-9_.-])', re.I)
PUBLIC_REPOS = {'liteharness', 'liteharness-plugin', 'litesuite', 'litetui', 'liteeditor', 'litespeak', 'liteterminal', 'liteimage'}


def digest(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()


def _mask_compatibility(text: str) -> str:
    def repository(match):
        value = match.group()
        parts = value.split('/')
        # Exact real repository handles/paths, not arbitrary profile URLs.
        if digest(parts[3]) == 'c81c698ef22c56c8e7ea449d32c7c29035e3ec98e89e80328d65de7368af74a2' and parts[4].removesuffix('.git').lower() in PUBLIC_REPOS:
            return '<public-repository-url>'
        return value
    text = REPO_URL.sub(repository, text)
    text = re.sub(r'(?<![A-Za-z0-9_-])([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)',
                  lambda m: '<public-repository-ref>' if digest(m[1]) == 'c81c698ef22c56c8e7ea449d32c7c29035e3ec98e89e80328d65de7368af74a2'
                  and m[2].removesuffix('.git').lower() in PUBLIC_REPOS else m[0], text)
    # The existing stable route is also published at one exact fixed loopback
    # endpoint. Mask only its prefix, never suffix/query/fragment content.
    endpoint = 'http://127.0.0.1:7423/v1/sentinel/assistant-message'
    text = re.sub(r'(?<![A-Za-z0-9_:/.-])' + re.escape(endpoint)
                  + r'(?![A-Za-z0-9_.-])', '<compatibility-endpoint>', text)
    for identifier in sorted(COMPATIBILITY, key=len, reverse=True):
        # Route-only spelling cannot turn near-match URLs into exemptions.
        before = r'(?<![A-Za-z0-9_:/.-])' if identifier.startswith('/') else r'(?<![A-Za-z0-9_-])'
        text = re.sub(before + re.escape(identifier) + r'(?![A-Za-z0-9_-])', '<compatibility-id>', text, flags=re.I)
    return text


def scan_text(text: str) -> list[tuple[int, str]]:
    text = _mask_compatibility(text)
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in HOME_RE.finditer(line):
            if match[1].lower() not in PLACEHOLDERS:
                hits.append((number, 'username path'))
        for label, pattern in PATH_PATTERNS:
            if pattern.search(line):
                hits.append((number, label))
        tokens = WORD_RE.findall(line)
        candidates = set(tokens) | {f'{a} {b}' for a, b in zip(tokens, tokens[1:])}
        for token in candidates:
            label = DENIED.get(digest(token))
            if label:
                hits.append((number, label))
    return sorted(set(hits))


def scan_file(relative: str, data: bytes) -> list[tuple[int, str]]:
    text = data.decode('utf-8', errors='replace')
    if relative in LEGAL_FIELDS:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON key')
                result[key] = value
            return result
        try:
            # Validate before granting any exception. Last-key-wins loses bytes.
            json.loads(text, object_pairs_hook=unique_object)
        except ValueError:
            return sorted(set(scan_text(text) + [(1, 'invalid or duplicate-key legal metadata')]))
        decoder = json.JSONDecoder()
        spans = []
        hits = []
        def whitespace(position):
            while position < len(text) and text[position] in ' \t\r\n':
                position += 1
            return position
        def decoded_hits(value, position):
            line = text.count('\n', 0, position) + 1
            hits.extend((line + offset - 1, label) for offset, label in scan_text(value))
        def walk(position, field=()):
            position = whitespace(position)
            if text[position] == '{':
                position = whitespace(position + 1)
                if text[position] == '}':
                    return position + 1
                while True:
                    key, end = decoder.raw_decode(text, position)
                    decoded_hits(key, position)
                    position = walk(whitespace(end) + 1, field + (key,))
                    position = whitespace(position)
                    if text[position] == '}':
                        return position + 1
                    position = whitespace(position + 1)
            if text[position] == '[':
                position = whitespace(position + 1)
                if text[position] == ']':
                    return position + 1
                index = 0
                while True:
                    position = whitespace(walk(position, field + (str(index),)))
                    if text[position] == ']':
                        return position + 1
                    position = whitespace(position + 1)
                    index += 1
            value, end = decoder.raw_decode(text, position)
            # Tuple paths prevent dotted keys from impersonating nested fields.
            approved = {tuple(path.split('.')) for path in LEGAL_FIELDS[relative]}
            if isinstance(value, str) and field in approved and digest(value) == LEGAL_AUTHOR:
                spans.append((position, end))
            elif isinstance(value, str):
                # Raw scan alone would miss JSON-escaped private identities.
                decoded_hits(value, position)
            else:
                # Scan every scalar too; exponent notation may hide an identity
                # from the raw word matcher. Raw numeric spelling is still kept.
                decoded_hits(str(value), position)
            return end
        walk(0)
        # Keep EVERY original byte outside exact approved string-value spans,
        # including numeric lexemes. Preserve newlines for raw finding locations.
        for start, end in reversed(spans):
            text = text[:start] + re.sub(r'[^\r\n]', ' ', text[start:end]) + text[end:]
        return sorted(set(scan_text(text) + hits))
    if relative == 'LICENSE':
        # Only the copyright attribution line is exempt; license body still scans.
        text = re.sub(r'^Copyright \(c\) \d{4} ([^\r\n]+)$',
                      lambda m: '<legal-copyright-attribution>' if digest(m[1]) == LEGAL_AUTHOR else m[0], text, flags=re.M)
    if relative == 'pyproject.toml':
        return _scan_project_toml(text)
    return scan_text(text)


def _scan_project_toml(text):
    # Parsing is mandatory before masking. Python 3.10 may use tomli; without
    # a parser we refuse rather than granting a line-shaped exception.
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        document = tomllib.loads(text)
    except (ImportError, ValueError):
        return sorted(set(scan_text(text) + [(1, 'invalid or unsupported legal TOML metadata')]))
    # Conservatively refuse masking whenever multiline strings/arrays could
    # make an apparent [project] header or assignment mere string content.
    ambiguous = any(marker in text for marker in (chr(34)*3, chr(39)*3))
    section = None
    spans = []
    allowed = set()
    offset = 0
    line_pattern = re.compile(r'(authors|maintainers)\s*=\s*\[\{\s*name\s*=\s*"([^"\n]+)"\s*,\s*email\s*=\s*"([^"\n]+)"\s*\}\]\s*')
    project = document.get('project', {})
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('['):
            section = 'project' if stripped == '[project]' else None
        match = line_pattern.fullmatch(stripped) if not ambiguous and section == 'project' else None
        if match and isinstance(project, dict):
            key, name, email = match.groups()
            expected = [{'name': name, 'email': email}]
            if (project.get(key) == expected and digest(name) == LEGAL_AUTHOR
                    and digest(email) == '3a1a74ad4cda051b58a5eff11deedeb54af70ac724d89ad9ac37543f57c515cc'):
                # Mask precisely the two attributed string values, not the line.
                for value in (name, email):
                    start = line.index('"' + value + '"') + offset + 1
                    spans.append((start, start + len(value)))
                allowed.update({('project', key, '0', 'name'), ('project', key, '0', 'email')})
        offset += len(line)
    hits = []
    def walk(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                hits.extend(scan_text(str(key)))
                walk(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + (str(index),))
        elif path not in allowed:
            hits.extend(scan_text(str(value)))
    walk(document)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + ' ' * (end-start) + text[end:]
    return sorted(set(scan_text(text) + hits))


def git_output(args: list[str]) -> bytes:
    result = subprocess.run(['git', *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('git ' + ' '.join(args) + ' failed; guard did not run')
    return result.stdout


def selftest() -> int:
    # Synthetic identities avoid putting denylist literals in shipping code/tests.
    actor = bytes.fromhex('73656e74696e656c').decode()
    human = bytes.fromhex('7279616e').decode()
    assert scan_text('Ask ' + actor)
    assert scan_text('human = ' + human)
    assert scan_file('notes.md', ('Copyright (c) 2026 ' + human).encode())
    assert not scan_file('LICENSE', ('Copyright (c) 2026 ' + human.title() + ' Devlin').encode())
    assert not scan_file('.codex-plugin/plugin.json', json.dumps({'author': {'name': human.title() + ' Devlin'}}).encode())
    assert scan_file('.codex-plugin/plugin.json', json.dumps({'actor': human}).encode())
    assert not scan_text('name: ls-' + actor)
    assert scan_text('actor = ' + actor)
    assert scan_file('image.bin', b'\0' + actor.encode())
    assert not scan_text('LiteHarness public product')
    print('SELFTEST OK: actor/legal-field/compatibility/binary positive and negative controls')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='scan all tracked worktree bytes')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        if git_output(['ls-files', '--unmerged', '-z']):
            raise RuntimeError('unmerged index; guard did not run')
        query = ['ls-files', '-z'] if args.all else ['diff', '--cached', '--name-only', '--diff-filter=d', '-z']
        names = [p.decode('utf-8') for p in git_output(query).split(b'\0') if p]
        if not names:
            print('ERROR: NOTHING SCANNED; no candidate files, not a pass')
            return 2
        scanned = 0
        hits = []
        for relative in names:
            data = Path(relative).read_bytes() if args.all else git_output(['show', ':' + relative])
            scanned += 1
            for line, label in scan_file(relative, data):
                hits.append((relative, line, label))
        notes = [hit for hit in hits if hit[2] in NOTES]
        if notes:
            print(f'note: {len(notes)} name mention(s), not blocking')
            hits = [hit for hit in hits if hit[2] not in NOTES]
        if hits:
            print(f'BLOCKED: {len(hits)} public privacy/actor findings ({scanned} files scanned)')
            for relative, line, label in hits:
                # Report location/category, never echo sensitive content into logs.
                print(f'  {relative}:{line} [{label}]')
            return 1
        print(f'check_pii: clean ({scanned} of {len(names)} offered files scanned, {"all" if args.all else "staged"} mode)')
        return 0
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f'ERROR: guard could not scan: {exc}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
