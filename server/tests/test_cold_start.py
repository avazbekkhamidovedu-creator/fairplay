import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_class_membership_on_a_fresh_database_with_scan_disabled(tmp_path):
    script = textwrap.dedent("""
        import sys
        from fastapi.testclient import TestClient
        from app.config import Settings
        from app.main import create_app

        folder = sys.argv[1]
        settings = Settings(database_url=f"sqlite+aiosqlite:///{folder}/fresh.db",
                            dev=True, seed_demo=True, seed_loadtest_users=1,
                            secret='cold-start-test-secret-0123456789', app_keys=['cold-start-test-app-key'],
                            pbkdf2_iterations=1000, features=[], single_process=True,
                            uploads_dir=f"{folder}/uploads")
        app = create_app(settings)
        assert 'app.scan.models' not in sys.modules
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post('/api/auth/login', json={'username': 'teacher', 'password': 'teacher123'})
            assert response.status_code == 200, response.text
            headers = {'Authorization': 'Bearer ' + response.json()['token']}
            response = client.post('/api/classes', json={'name': 'Cold-start class'}, headers=headers)
            assert response.status_code == 201, response.text
            class_id = response.json()['id']
            response = client.post(f'/api/classes/{class_id}/members',
                                   json={'usernames': ['alice', 'lt0001']}, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json() == {'added': 1, 'already': 0, 'unknown': ['lt0001']}, response.text
            assert client.get('/api/health').json()['features'] == []
            assert client.get('/api/scan/exams', headers=headers).status_code == 404
    """)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in os.environ}
    env.update(FAIRPLAY_FEATURES='', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
