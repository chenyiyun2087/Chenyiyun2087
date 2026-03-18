import unittest

from project_network import build_direct_network_env


class TestProjectNetwork(unittest.TestCase):
    def test_build_direct_network_env_removes_proxy_keys(self):
        env = build_direct_network_env(
            {
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "NO_PROXY": "example.com",
                "KEEP_ME": "1",
            },
            pythonpath_prefix="/tmp/project",
        )

        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["KEEP_ME"], "1")
        self.assertEqual(env["PROJECT_DIRECT_NETWORK"], "1")
        self.assertIn("example.com", env["NO_PROXY"])
        self.assertIn("*", env["NO_PROXY"])
        self.assertIn("localhost", env["NO_PROXY"])
        self.assertEqual(env["NO_PROXY"], env["no_proxy"])
        self.assertEqual(env["PYTHONPATH"], "/tmp/project")


if __name__ == "__main__":
    unittest.main()
