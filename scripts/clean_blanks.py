#!/usr/bin/env python3
#
# Script to clean blank messages from DB.
#

from sqlalchemy import create_engine, text

DB_URI = 'postgresql+psycopg://dsidlo:rexrabbit@127.0.0.1:5433/honcho'

engine = create_engine(DB_URI, echo=False)

with engine.connect() as conn:
    # Count blanks
    count_stmt = text('SELECT COUNT(*) FROM messages WHERE content IS NULL OR length(trim(coalesce(content, \\\"\\\"))) = 0')
    count = conn.execute(count_stmt).scalar()
    print(f'Found {count} blank messages')
    
    if count > 0:
        # Delete blanks
        delete_stmt = text('DELETE FROM messages WHERE content IS NULL OR length(trim(coalesce(content, \\\"\\\"))) = 0')
        deleted = conn.execute(delete_stmt).rowcount
        conn.commit()
        print(f'Deleted {deleted} blank messages')
    else:
        print('No blanks to delete')

print('Clean complete')
