import re
import unittest

from tests.helpers import ROOT, assert_contains_all, read_text


class PackagingTest(unittest.TestCase):
    def test_project_metadata_is_release_ready(self):
        pyproject = read_text("pyproject.toml")
        assert_contains_all(
            self,
            pyproject,
            (
                'name = "paperseek"',
                'version = "0.2.1"',
                'Homepage = "https://www.paperseek.xyz/"',
                'Repository = "https://github.com/MingfengHong/paperseek"',
                'Documentation = "https://docs.paperseek.xyz/"',
            ),
        )
        self.assertNotIn("modelscope.cn/studios", pyproject)
        self.assertNotRegex(pyproject, re.compile(r"^wos-search\s*=", re.MULTILINE))
        self.assertNotRegex(pyproject, re.compile(r"^wos-search-web\s*=", re.MULTILINE))

    def test_federated_extra_is_optional_and_gated(self):
        # MOSAIC must stay an opt-in extra restricted to Python >= 3.11 so a
        # base install (>=3.8) never pulls it and never breaks.
        pyproject = read_text("pyproject.toml")
        self.assertIn("federated = [", pyproject)
        self.assertIn("mosaic-search>=1.5.5", pyproject)
        self.assertIn("python_version >= '3.11'", pyproject)
        # No non-optional mention of mosaic in core runtime dependencies.
        dependencies = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
        self.assertNotIn("mosaic", dependencies)
        # requirements.txt mirrors the base install only.
        requirements = read_text("requirements.txt")
        self.assertNotIn("mosaic", requirements.lower())
        # Forbidden heavyweight extras must not ride along with federated.
        federated_extra = pyproject.split("federated = [", 1)[1].split("]", 1)[0]
        for forbidden in ("playwright", "notebooklm", "flask", "sqlite-vec", "scrapy", "selenium"):
            self.assertNotIn(forbidden, federated_extra)

    def test_readmes_cover_installation_and_primary_links(self):
        expected_install_paths = (
            "python -m pip install paperseek",
            "git clone https://github.com/MingfengHong/paperseek.git",
            "python -m pip install -e .",
        )
        expected_links = (
            "https://pypi.org/project/paperseek/",
            "https://github.com/MingfengHong/paperseek",
            "docs/assets/paperseek-web.png",
            "docs/user-manual.md",
            "docs/deployment.md",
            "docs/online-demo.md",
            "https://www.paperseek.xyz/",
        )
        for path in ("README.md", "README.en.md"):
            readme = read_text(path)
            with self.subTest(path=path):
                self.assertFalse(readme.startswith("---\n"))
                self.assertNotIn("<repo-url>", readme)
                assert_contains_all(self, readme, expected_install_paths)
                assert_contains_all(self, readme, expected_links)
                self.assertNotIn("https://modelscope.cn/studios/HongMingfeng/paperseek", readme)

    def test_readmes_acknowledge_reference_projects(self):
        expected_links = (
            "https://github.com/dr-dumpling/paper-search-cli/",
            "https://github.com/666ghj/MiroFish",
            "https://github.com/clarivate/wosstarter_python_client",
            "https://github.com/Lloyd-Jahn/openclaw-paper-search",
        )
        for path in ("README.md", "README.en.md"):
            readme = read_text(path)
            with self.subTest(path=path):
                assert_contains_all(self, readme, expected_links)

    def test_user_manual_covers_main_workflows(self):
        manual = read_text("docs/user-manual.md")
        assert_contains_all(
            self,
            manual,
            (
                "## Get started",
                "## Install",
                "## Configuration",
                "## Models",
                "## Data sources",
                "## CLI",
                "## Web UI",
                "## Deployment",
                "## Agent Skill",
                "## MCP Server",
                "## Diagnostics and troubleshooting",
                "python -m pip install paperseek",
                "git clone https://github.com/MingfengHong/paperseek.git",
            ),
        )

    def test_online_demo_guide_exists(self):
        guide = read_text("docs/online-demo.md")
        assert_contains_all(
            self,
            guide,
            (
                "https://www.paperseek.xyz/",
                "ModelScope",
                "API Inference",
                "历史记录按登录账号隔离",
            ),
        )

    def test_docs_site_files_exist(self):
        for path in (
            "docs/.vitepress/config.mts",
            "docs/public/README.md",
            "docs/deployment.md",
            "docs/assets/deploy-modelscope.svg",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

    def test_deployment_entry_files_exist(self):
        for path in (
            "Dockerfile",
            ".dockerignore",
            "docker-compose.yml",
            "app.py",
            "api/index.py",
            "vercel.json",
            "ms_deploy.json",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())


if __name__ == "__main__":
    unittest.main()
