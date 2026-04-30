import os
import subprocess
from pathlib import Path
import pytest
import logging
from fastapi.testclient import TestClient
import sys

# Must run before app import to inject fake env for the log test
os.environ["WEBUI_ADMIN_PASSWORD"] = "SUPER_SECRET_PASSWORD_123!"
os.environ["TELEGRAM_TOKEN"] = "12345:SUPER_SECRET_TOKEN"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app

def test_env_files_not_tracked_by_git():
    project_root = Path(__file__).resolve().parents[2]
    
    for env_file in [".env", ".env.postgres"]:
        file_path = project_root / env_file
        
        # If git is available, check that the file is not tracked
        try:
            output = subprocess.check_output(
                ["git", "ls-files", env_file], 
                cwd=project_root, 
                text=True,
                stderr=subprocess.DEVNULL
            )
            assert output.strip() == "", f"Security risk: {env_file} is tracked by git!"
        except subprocess.CalledProcessError:
            pass # git not available or failed, skip git check

def test_env_example_has_no_secrets():
    project_root = Path(__file__).resolve().parents[2]
    
    for example_file in [".env.example", ".env.postgres.example"]:
        file_path = project_root / example_file
        if not file_path.exists():
            continue
            
        content = file_path.read_text(encoding="utf-8")
        
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
                
            if "=" in line:
                key, value = line.split("=", 1)
                value = value.strip()
                
                # Check for suspect values: long, alphanumeric, no spaces
                if key.endswith("_MODEL") or key.endswith("_FALLBACKS"):
                    continue
                    
                if value.startswith("http://") or value.startswith("https://") or value.startswith("redis://") or value.startswith("postgres://"):
                    continue
                    
                if len(value) > 20 and " " not in value and "replace_" not in value and "change_" not in value:
                    pytest.fail(f"Potential secret found in {example_file}: {key}={value}")

def test_app_does_not_log_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    
    secret_pass = os.environ.get("WEBUI_ADMIN_PASSWORD")
    secret_token = os.environ.get("TELEGRAM_TOKEN")
    
    with TestClient(app) as client:
        # Trigger an error to see if logs leak secrets
        response = client.get("/login", headers={"Authorization": f"Bearer {secret_token}"})
        
        # Check logs
        assert secret_pass not in caplog.text, "Secret password leaked in logs!"
        assert secret_token not in caplog.text, "Secret token leaked in logs!"
