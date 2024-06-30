FROM python:3.10.5-slim-buster
WORKDIR /opt/server

RUN echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye main contrib non-free\
    deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-updates main contrib non-free\
    deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-backports main contrib non-free\
    deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bullseye-security main contrib non-free' > /etc/apt/sources.list \
    && apt update \
    && apt-get install --no-install-recommends -y libmagic-dev nginx procps gcc nodejs npm pkg-config \
    && npm config set registry https://registry.npmmirror.com \
    && npm install pm2 -g \
    && if [ ! -d /var/log/nginx ]; then mkdir /var/log/nginx; fi  

ARG a=1
ADD requirements.txt .
RUN pip3 install -i  https://pypi.tuna.tsinghua.edu.cn/simple  --no-cache-dir -r requirements.txt 

ADD . .

RUN cp -R config/nginx/* /etc/nginx/  \
    && chown -R www-data:www-data /etc/nginx \
    && unlink /etc/nginx/sites-enabled/default \
    && rm -rf configs/nginx

EXPOSE 8000

CMD ["/bin/bash", "config/entrypoints.sh"]