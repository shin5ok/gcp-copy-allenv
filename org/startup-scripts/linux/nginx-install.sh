#!/bin/bash
# Install Nginx and curl
apt-get update
apt-get install -y nginx curl

# Get host info
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}')

# Configure default index page
echo -e "Hostname: ${HOSTNAME}\nIP: ${IP}" > /var/www/html/index.html

# Overwrite Nginx configuration for /json
cat <<'EOF' > /etc/nginx/sites-available/default
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/html;
    index index.html index.htm;

    server_name _;

    location / {
        default_type text/plain;
        try_files $uri $uri/ =404;
    }

    location /json {
        default_type application/json;
        return 200 '{"hostname": "HOSTNAME_PLACEHOLDER", "ip": "IP_PLACEHOLDER"}\n';
    }
}
EOF

# Replace placeholders with actual values
sed -i "s/HOSTNAME_PLACEHOLDER/${HOSTNAME}/" /etc/nginx/sites-available/default
sed -i "s/IP_PLACEHOLDER/${IP}/" /etc/nginx/sites-available/default

# Apply configuration
systemctl restart nginx
