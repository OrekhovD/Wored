setup:
	cp .env.example .env || copy .env.example .env
	python scripts/generate_secrets.py

build:
	docker-compose build

up:
	docker-compose up -d
	down:
	docker-compose down

logs:
	docker-compose logs -f

ps:
	docker-compose ps

db-shell:
	docker-compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB}

redis-cli:
	docker-compose exec redis redis-cli

backup:
	mkdir -p backups
	docker-compose exec postgres pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} > backups/db_backup_$(date +\%Y-\%m-\%d).sql

restore:
	ls backups
	echo "Введите имя файла для восстановления (например, db_backup_2023-04-18.sql):"
	read BACKUP_FILE
	docker-compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} < backups/${BACKUP_FILE}

clean:
	docker-compose down -v
	rm -rf backups
	rm -rf .venv
