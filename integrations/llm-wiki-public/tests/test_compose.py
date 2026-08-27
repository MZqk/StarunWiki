import json
import subprocess
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
WEKNORA = PROJECT.parents[1] / "services" / "WeKnora"


class ComposeTests(unittest.TestCase):
    def effective_config(self) -> dict:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "llm-wiki-public",
                "--env-file",
                str(PROJECT / ".env"),
                "-f",
                str(WEKNORA / "docker-compose.yml"),
                "-f",
                str(PROJECT / "docker-compose.local.yml"),
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_default_runtime_is_sqlite_only(self) -> None:
        config = self.effective_config()
        services = config["services"]
        self.assertNotIn("postgres", services)
        self.assertNotIn("redis", services)
        self.assertNotIn("frontend", services)

        app = services["app"]
        environment = app["environment"]
        self.assertEqual(environment["DB_DRIVER"], "sqlite")
        self.assertEqual(environment["DB_PATH"], "/data/weknora/weknora.db")
        self.assertEqual(environment["RETRIEVE_DRIVER"], "sqlite")
        self.assertEqual(environment["REDIS_ADDR"], "")
        self.assertEqual(environment["STREAM_MANAGER_TYPE"], "memory")
        self.assertEqual(set(app["depends_on"]), {"docreader"})

        bff_environment = services["public-bff"]["environment"]
        self.assertEqual(bff_environment["PUBLIC_DB_PATH"], "/data/public-bff/public-bff.db")
        self.assertFalse(any(key.startswith("REDIS_") for key in bff_environment))
        self.assertEqual(set(services["public-bff"]["depends_on"]), {"app"})

        self.assertIn("weknora-sqlite-data", config["volumes"])
        self.assertIn("public-bff-sqlite-data", config["volumes"])


if __name__ == "__main__":
    unittest.main()
