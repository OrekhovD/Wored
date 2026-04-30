import argparse
import secrets
import string
import os

def generate_secure_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and sum(c.isdigit() for c in password) >= 3):
            return password

def generate_session_secret(length: int = 64) -> str:
    return secrets.token_hex(length // 2)

def main():
    parser = argparse.ArgumentParser(description="Generate secure secrets for WORED")
    parser.add_argument("--dry-run", action="store_true", help="Only print generated values, do not write files")
    args = parser.parse_args()

    postgres_password = generate_secure_password()
    webui_admin_password = generate_secure_password()
    webui_session_secret = generate_session_secret()

    print("\n" + "="*50)
    print("[+] Сгенерированные безопасные ключи")
    print("="*50)
    print("\n[Postgres]")
    print(f"POSTGRES_PASSWORD={postgres_password}")
    print("\n[WebUI]")
    print(f"WEBUI_ADMIN_PASSWORD={webui_admin_password}")
    print(f"WEBUI_SESSION_SECRET={webui_session_secret}")
    print("\n" + "="*50)

    if args.dry_run:
        print("Dry run mode. Files not created/modified.")
        return

    # Helper to generate .env.postgres if it doesn't exist
    postgres_env_path = ".env.postgres"
    if not os.path.exists(postgres_env_path):
        with open(postgres_env_path, "w", encoding="utf-8") as f:
            f.write("POSTGRES_USER=bot\n")
            f.write(f"POSTGRES_PASSWORD={postgres_password}\n")
            f.write("POSTGRES_DB=trading\n")
        print(f"[OK] Создан {postgres_env_path}")
    else:
        print(f"[INFO] Файл {postgres_env_path} уже существует. Пропущен.")

    print("\nДля обновления .env скопируйте значения выше и вставьте их в свой .env файл.")

if __name__ == "__main__":
    main()
