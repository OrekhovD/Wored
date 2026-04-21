#!/usr/bin/env python3
"""
Weekly Refresh Script for Hypercube Project.

Generates weekly documentation updates:
- CHANGELOG_WEEKLY.md (append new week)
- docs/KNOWN_LIMITS.md (update status)
- docs/PROVIDER_DIFF.md (update metrics)
- docs/DEPRECATIONS.md (add new deprecations)

Usage:
    python scripts/weekly_refresh.py [--week YYYY-WW] [--dry-run]
"""
import argparse
import datetime
import os
import sys
from pathlib import Path


def get_current_week() -> tuple[int, int]:
    """Get current ISO year and week number."""
    today = datetime.date.today()
    iso_calendar = today.isocalendar()
    return iso_calendar.year, iso_calendar.week


def get_week_dates(year: int, week: int) -> tuple[datetime.date, datetime.date]:
    """Get Monday and Sunday dates for given ISO week."""
    # Find the Monday of the given week
    jan4 = datetime.date(year, 1, 4)
    start_of_week1 = jan4 - datetime.timedelta(days=jan4.weekday())
    monday = start_of_week1 + datetime.timedelta(weeks=week - 1)
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def generate_week_header(year: int, week: int) -> str:
    """Generate week header for changelog."""
    monday, sunday = get_week_dates(year, week)
    return f"## Week {week}: {year}-{monday.strftime('%m-%d')} — Weekly Refresh"


def check_file_exists(filepath: str) -> bool:
    """Check if file exists."""
    return os.path.exists(filepath)


def count_tests() -> dict[str, int]:
    """Count tests in test directories."""
    test_dirs = [
        'tests/unit',
        'tests/integration',
        'tests/smoke',
    ]
    
    counts = {
        'unit': 0,
        'integration': 0,
        'smoke': 0,
        'total': 0,
    }
    
    for test_dir in test_dirs:
        if not os.path.exists(test_dir):
            continue
        
        category = test_dir.split('/')[1]
        for file in os.listdir(test_dir):
            if file.startswith('test_') and file.endswith('.py'):
                filepath = os.path.join(test_dir, file)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    test_count = content.count('def test_')
                    counts[category] += test_count
                    counts['total'] += test_count
    
    return counts


def count_documents() -> int:
    """Count markdown documents in docs/."""
    docs_dir = 'docs'
    if not os.path.exists(docs_dir):
        return 0
    
    count = 0
    for file in os.listdir(docs_dir):
        if file.endswith('.md'):
            count += 1
    return count


def get_git_status() -> dict[str, int]:
    """Get git statistics (if available)."""
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'log', '--oneline', '--since="1 week ago"'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        commits = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
        
        result = subprocess.run(
            ['git', 'diff', '--shortstat', 'HEAD~7..HEAD'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        return {
            'commits': commits,
            'status': 'available'
        }
    except Exception:
        return {
            'commits': 0,
            'status': 'unavailable'
        }


def generate_weekly_summary(year: int, week: int) -> str:
    """Generate weekly summary section."""
    test_counts = count_tests()
    doc_count = count_documents()
    git_stats = get_git_status()
    
    summary = f"""
### 📊 Статистика недели

| Метрика | Значение |
|---------|----------|
| Неделя | {year}-W{week:02d} |
| Документов | {doc_count} |
| Тестов всего | {test_counts['total']} |
| └─ Unit тесты | {test_counts['unit']} |
| └─ Integration тесты | {test_counts['integration']} |
| └─ Smoke тесты | {test_counts['smoke']} |
| Git коммитов | {git_stats['commits']} ({git_stats['status']}) |
| Статус релиза | 🟡 Beta (hardening в процессе) |
"""
    return summary


def update_changelog_weekly(year: int, week: int, dry_run: bool = False) -> None:
    """Update CHANGELOG_WEEKLY.md with new week section."""
    filepath = 'CHANGELOG_WEEKLY.md'
    
    header = generate_week_header(year, week)
    summary = generate_weekly_summary(year, week)
    
    new_section = f"""
---

{header}

### 🔄 Weekly Refresh — АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ

Этот раздел создан автоматически скриптом weekly_refresh.py.

**Задачи еженедельного обновления:**
- [ ] Проверить и обновитьKNOWN_LIMITS.md
- [ ] Проверить и обновить PROVIDER_DIFF.md
- [ ] Проверить и обновить DEPRECATIONS.md
- [ ] Добавить изменения за неделю
- [ ] Обновить статистику недели
- [ ] Обновить план на следующую неделю
{summary}
### 📝 Задачи на неделю

**WP-8: Weekly Refresh Activation**
- [ ] Активировать скрипт еженедельного обновления
- [ ] Настроить расписание (понедельник 09:00 Asia/Bangkok)
- [ ] Проверить работу скрипта
- [ ] Обновить CHANGELOG_WEEKLY.md

**Оставшиеся задачи:**
- [ ] Добавить тесты для Telegram бота
- [ ] Добавить mock провайдеров
- [ ] Настроить CI/CD пайплайн
- [ ] Интеграционные тесты для HTX adapter

---

## 📅 Расписание еженедельных обновлений

Следующее обновление: **{year}-{(datetime.date.today() + datetime.timedelta(weeks=1)).strftime('%m-%d')} 09:00 Asia/Bangkok**

### План на Week {week + 1} ({year}-{(datetime.date.today() + datetime.timedelta(weeks=1)).strftime('%m-%d')})
- [ ] Завершить WP-8: Weekly Refresh Activation
- [ ] Подготовить release checklist
- [ ] Подготовить go/no-go report
- [ ] Продолжить работу над тестами

---

## 📝 Примечания

1. **Обновления публикуются** каждый понедельник в 09:00 Asia/Bangkok
2. **Изменения группируются** по WP (Work Package)
3. **Статус Go/No-Go** определяется перед каждым релизом
4. **Оставшиеся задачи** переносятся на следующую неделю
"""
    
    if dry_run:
        print(f"[DRY RUN] Would update {filepath}")
        print(new_section[:500] + "...")
        return
    
    # Read existing content
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            existing_content = f.read()
    else:
        existing_content = "# Changelog Weekly\n\nЕженедельный журнал изменений проекта Hypercube (Telegram AI Gateway для HTX).\n"
    
    # Append new section
    updated_content = existing_content + new_section
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    print(f"✅ Updated {filepath}")


def update_known_limits(year: int, week: int, dry_run: bool = False) -> None:
    """Update docs/KNOWN_LIMITS.md with current date."""
    filepath = 'docs/KNOWN_LIMITS.md'
    
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} does not exist, skipping")
        return
    
    if dry_run:
        print(f"[DRY RUN] Would update {filepath} with today's date")
        return
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update date in updates section
    today = datetime.date.today().strftime('%Y-%m-%d')
    if f"| {today} |" not in content:
        # Add new date to updates table
        old_line = "| 2026-04-21 | Первоначальное создание документа |"
        new_line = f"| {today} | Еженедельное обновление W{week} |\n{old_line}"
        content = content.replace(old_line, new_line)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Updated {filepath}")


def update_provider_diff(year: int, week: int, dry_run: bool = False) -> None:
    """Update docs/PROVIDER_DIFF.md with current date."""
    filepath = 'docs/PROVIDER_DIFF.md'
    
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} does not exist, skipping")
        return
    
    if dry_run:
        print(f"[DRY RUN] Would update {filepath} with today's date")
        return
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update date in updates section
    today = datetime.date.today().strftime('%Y-%m-%d')
    if f"| {today} |" not in content:
        old_line = "| 2026-04-21 | Первоначальное создание документа |"
        new_line = f"| {today} | Еженедельное обновление W{week} |\n{old_line}"
        content = content.replace(old_line, new_line)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Updated {filepath}")


def update_deprecations(year: int, week: int, dry_run: bool = False) -> None:
    """Update docs/DEPRECATIONS.md with current date."""
    filepath = 'docs/DEPRECATIONS.md'
    
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} does not exist, skipping")
        return
    
    if dry_run:
        print(f"[DRY RUN] Would update {filepath} with today's date")
        return
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update date in updates section
    today = datetime.date.today().strftime('%Y-%m-%d')
    if f"| {today} |" not in content:
        old_line = "| 2026-04-21 | Первоначальное создание документа |"
        new_line = f"| {today} | Еженедельное обновление W{week} |\n{old_line}"
        content = content.replace(old_line, new_line)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Updated {filepath}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Weekly Refresh Script for Hypercube Project'
    )
    parser.add_argument(
        '--week',
        type=str,
        default=None,
        help='ISO week in format YYYY-WW (default: current week)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    
    args = parser.parse_args()
    
    # Parse week or use current
    if args.week:
        try:
            year, week = map(int, args.week.split('-'))
        except ValueError:
            print("Error: Week format should be YYYY-WW (e.g., 2026-17)")
            sys.exit(1)
    else:
        year, week = get_current_week()
    
    print(f"🔄 Weekly Refresh for Week {week}, {year}")
    print(f"{'='*60}")
    
    # Update all weekly documents
    update_changelog_weekly(year, week, args.dry_run)
    update_known_limits(year, week, args.dry_run)
    update_provider_diff(year, week, args.dry_run)
    update_deprecations(year, week, args.dry_run)
    
    print(f"{'='*60}")
    if args.dry_run:
        print("✅ Dry run complete. No files were modified.")
    else:
        print("✅ Weekly refresh complete!")
        print("\n📝 Next steps:")
        print("   1. Review changes in CHANGELOG_WEEKLY.md")
        print("   2. Update task statuses in each document")
        print("   3. Commit changes with message: 'Weekly refresh W{week}'")


if __name__ == '__main__':
    main()
