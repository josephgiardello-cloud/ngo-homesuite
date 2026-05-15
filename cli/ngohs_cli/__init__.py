import sys
from .migrate import migrate

def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        migrate()
    else:
        print('Usage: homesuite migrate')
