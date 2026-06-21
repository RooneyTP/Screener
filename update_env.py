with open('.env', 'r') as f:
    c = f.read()
c = c.replace('TELEGRAM_BOT_TOKEN=*** 'TELEGRAM_BOT_TOKEN=12334 = c.replace('TELEGRAM_CHAT_ID=*** 'TELEGRAM_CHAT_ID=12334')
with open('.env', 'w') as f:
    f.write(c)
print('Done')
