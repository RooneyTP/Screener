with open('.env', 'r') as f:
    c = f.read()
c = c.replace('TELEGRAM_BOT_TOKEN=*** 'TELEGRAM_BOT_TOKEN=810171...c = c.replace('TELEGRAM_CHAT_ID=*** 'TELEGRAM_CHAT_ID=-5237365204')
with open('.env', 'w') as f:
    f.write(c)
print('Done')
