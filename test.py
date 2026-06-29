import sqlite3
conn = sqlite3.connect('restaurant.db')
conn.execute("ALTER TABLE orders ADD COLUMN room_id TEXT")
conn.commit()
conn.close()