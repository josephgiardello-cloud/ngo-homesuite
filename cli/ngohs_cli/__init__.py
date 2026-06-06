import sys
from .migrate import migrate
from .minion_index import reindex

def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        migrate()
    elif len(sys.argv) > 1 and sys.argv[1] == 'reindex':
        reindex()
    else:
        print('Usage: homesuite [migrate|reindex]')

