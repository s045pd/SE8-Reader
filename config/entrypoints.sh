#!/bin/bash
sleep 5
python3 manage.py check
python3 manage.py createcachetable
python3 manage.py collectstatic --noinput
python3 manage.py migrate
python manage.py shell -c "
import os
from django.contrib.auth.models import User
import random
import string

username = os.getenv('DJANGO_SUPERUSER_USERNAME') or 'admin_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
email = username + '@example.com'
password = os.getenv('DJANGO_SUPERUSER_PASSWORD') or ''.join(random.choices(string.ascii_letters + string.digits, k=12))

User.objects.create_superuser(username=username, email=email, password=password)

print(f'\n\r\n\r\n\rSuperuser created with username: {username} and password: {password}\n\r\n\r\n\r')
"
nginx
pm2 start config/ecosystem.config.js
pm2 logs
