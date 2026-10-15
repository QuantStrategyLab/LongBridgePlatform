"""Synthetic offline fixtures; never import broker/runtime dependencies."""
import subprocess
import tempfile
import sys
import html
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import review_shared_dependency_delta as delta

BASE = "350dd38ece0952beef096893a7dcf8ae3871609a"
HEAD = "155d74a615ccdebf7223787939c2dc7151d75efd"


def manifests(pin=BASE):
    dependencies = []
    packages = []
    for name, repository in delta.REPOSITORIES.items():
        url = f"https://github.com/QuantStrategyLab/{repository}.git"
        dependencies.append(f'"{name} @ git+{url}@{pin}"')
        packages.append(f'[[package]]\nname = "{name}"\nsource = {{ git = "{url}?rev={pin}#{pin}" }}')
    return '[project]\ndependencies = [' + ','.join(dependencies) + ']\n', '\n'.join(packages)


class DependencyDeltaTests(unittest.TestCase):
    def test_exact_manifest_pins(self):
        self.assertEqual(set(delta.resolve_pins(*manifests()).values()), {BASE})

    def test_missing_sha_and_lock_mismatch(self):
        project, lock = manifests()
        for bad_project, bad_lock in ((project.replace(BASE, 'main'), lock),
                                      (project, lock.replace(BASE, HEAD)),
                                      (project, lock.replace('#' + BASE, ''))):
            with self.subTest(lock=bad_lock):
                with self.assertRaises(delta.Incomplete):
                    delta.resolve_pins(bad_project, bad_lock)

    def test_no_change_never_fetches(self):
        pins = delta.resolve_pins(*manifests())
        def forbidden(*args):
            self.fail('No-change comparison attempted network')
        self.assertIn('No pin change', delta.report(pins, pins, forbidden))

    def test_350dd38_to_155d74a_auth_and_facade_fixture(self):
        base = delta.resolve_pins(*manifests())
        head = dict(base, **{'quant-platform-kit': HEAD})
        paths = ['src/quant_platform_kit/longbridge/auth.py',
                 'src/quant_platform_kit/common/presentation/value_target_plan.py',
                 'src/quant_platform_kit/execution_kernel/guards.py',
                 'src/quant_platform_kit/strategy_lifecycle/watch/runner.py',
                 'docs/facade.md', 'tests/test_longbridge_auth.py']
        result = delta.report(base, head, lambda *args: paths)
        for expected in paths + list(delta.FLAGS) + [BASE, HEAD, 'unresolved']:
            self.assertIn(expected, html.unescape(result))

    def test_docs_only_and_unknown_production_not_safe(self):
        base, head = delta.resolve_pins(*manifests()), delta.resolve_pins(*manifests(HEAD))
        docs = delta.report(base, head, lambda *args: ['docs/change.md'])
        self.assertIn('Documentation-only delta', docs)
        unknown = delta.report(base, head, lambda *args: ['src/dynamic_plugin.py'])
        self.assertIn('src/dynamic_plugin.py', html.unescape(unknown))
        self.assertIn('not proof of safety', unknown)
        self.assertNotIn('Documentation-only delta', unknown)

    def test_failed_compare_is_incomplete(self):
        def fail(*args):
            raise delta.Incomplete('comparison failed')
        result = delta.report(delta.resolve_pins(*manifests()),
                              delta.resolve_pins(*manifests(HEAD)), fail)
        self.assertIn('INCOMPLETE', result)
        self.assertNotIn('Comparison: complete', result)

    def test_non_utf8_path_is_incomplete_not_collapsed(self):
        with patch.object(delta, 'run_git', return_value=b'src/\xff.py\0src/\\xff.py\0'):
            with self.assertRaises(delta.Incomplete):
                delta.changed_paths(Path('.'), BASE, HEAD)

    def test_truncated_output_is_incomplete(self):
        with patch.object(delta, 'run_git', return_value=b'path-without-nul'):
            with self.assertRaises(delta.Incomplete):
                delta.changed_paths(Path('.'), BASE, HEAD)

    def test_refs_and_markdown_paths_are_not_executable(self):
        with self.assertRaises(delta.Incomplete):
            delta.changed_paths(Path('.'), '--help', HEAD)
        hostile = '<img>\n`$(command)`[SAFE](https://example.invalid)![pic](img)*_'
        rendered = delta.quoted(hostile)
        self.assertEqual(html.unescape(rendered), '<code>' + hostile.encode('unicode_escape').decode() + '</code>')
        for marker in '[]()!*_`':
            self.assertNotIn(marker, rendered)

    def test_alternate_override_and_duplicate_direct_declarations(self):
        project, lock = manifests()
        for override in ('quant-platform-kit==999',
                         'quant_platform_kit @ git+https://github.com/QuantStrategyLab/QuantPlatformKit.git@' + HEAD):
            with self.subTest(override=override), self.assertRaises(delta.Incomplete):
                delta.resolve_pins(project + '[tool.uv]\noverride-dependencies = ["' + override + '"]', lock)
        duplicate = project.replace('dependencies = [', 'dependencies = ["quant_platform_kit==999",')
        with self.assertRaises(delta.Incomplete):
            delta.resolve_pins(duplicate, lock)


    def test_rendered_summary_overflow_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.md'
            args = ['report', '--base-ref', BASE, '--head-ref', HEAD, '--output', str(output)]
            with patch.object(sys, 'argv', args), patch.object(delta, 'pins_at', return_value={}), \
                    patch.object(delta, 'report', return_value='界' * 400_000):
                self.assertEqual(delta.main(), 0)
            result = output.read_text()
            self.assertIn('INCOMPLETE', result)
            self.assertLess(len(result.encode()), 900_000)

    def test_real_git_deleted_renamed_and_unusual_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Offline fixture')
            (repo / 'old.py').write_text('fixture')
            git('add', '.')
            git('commit', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            (repo / 'old.py').rename(repo / 'new\npath.py')
            git('add', '.')
            git('commit', '-m', 'head')
            self.assertEqual(delta.changed_paths(repo, base, git('rev-parse', 'HEAD')),
                             ['new\npath.py', 'old.py'])
            with patch.object(delta, 'MAX_OUTPUT', 1):
                with self.assertRaises(delta.Incomplete):
                    delta.changed_paths(repo, base, git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
