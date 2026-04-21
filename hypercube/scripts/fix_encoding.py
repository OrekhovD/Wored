"""Fix file encoding to UTF-8 for all documentation and source files."""
import os
import chardet
from pathlib import Path

def detect_encoding(file_path: str) -> str:
    """Detect file encoding."""
    with open(file_path, 'rb') as f:
        result = chardet.detect(f.read(100000))  # Read first 100KB
    return result.get('encoding', 'utf-8')

def fix_encoding(file_path: str, target_encoding: str = 'utf-8') -> bool:
    """Fix file encoding to target encoding.
    
    Returns:
        True if file was fixed, False if already correct or error
    """
    try:
        # Detect current encoding
        current_encoding = detect_encoding(file_path)
        
        # Skip if already UTF-8
        if current_encoding and 'utf' in current_encoding.lower():
            print(f"✓ {file_path}: Already UTF-8 (detected: {current_encoding})")
            return False
        
        # Read with detected encoding
        with open(file_path, 'r', encoding=current_encoding or 'cp1251') as f:
            content = f.read()
        
        # Write with UTF-8
        with open(file_path, 'w', encoding=target_encoding) as f:
            f.write(content)
        
        print(f"✓ {file_path}: Fixed from {current_encoding} to {target_encoding}")
        return True
    
    except Exception as e:
        print(f"✗ {file_path}: Error - {e}")
        return False

def main():
    """Fix encoding for all docs and source files."""
    root = Path(__file__).parent.parent
    
    # Directories to process
    dirs_to_fix = [
        root / 'docs',
        root / 'app',
        root / 'bot',
        root / 'core',
        root / 'providers',
        root / 'routing',
        root / 'context',
        root / 'accounting',
        root / 'quotas',
        root / 'storage',
        root / 'admin',
        root / 'tests',
    ]
    
    # File extensions to fix
    extensions = ['.md', '.py', '.txt', '.yaml', '.yml', '.json', '.toml', '.ini']
    
    fixed_count = 0
    total_count = 0
    
    for directory in dirs_to_fix:
        if not directory.exists():
            print(f"⊘ {directory}: Does not exist, skipping")
            continue
        
        print(f"\n📁 Processing {directory}...")
        
        for file_path in directory.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                # Skip binary files and __pycache__
                if '__pycache__' in str(file_path) or file_path.suffix in ['.pyc', '.pyo']:
                    continue
                
                total_count += 1
                if fix_encoding(str(file_path)):
                    fixed_count += 1
    
    print(f"\n{'='*60}")
    print(f"Encoding fix complete!")
    print(f"  Total files checked: {total_count}")
    print(f"  Files fixed: {fixed_count}")
    print(f"  Files already UTF-8: {total_count - fixed_count}")

if __name__ == '__main__':
    main()
