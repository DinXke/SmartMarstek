# SmartMarstek Standalone Deployment

This guide explains how to deploy SmartMarstek standalone on a fresh Ubuntu machine using Docker Compose.

## Quick Start (One Command)

```bash
curl -fsSL https://raw.githubusercontent.com/SmartMarstek/GRIP/main/install.sh | bash
```

This script will:
1. Check/install Docker and docker-compose
2. Clone the SmartMarstek repository
3. Generate `.env` configuration
4. Start all services (SmartMarstek, InfluxDB, Grafana, Nginx)
5. Display access URLs

> **Estimated time:** 2-5 minutes

## Manual Setup

If you prefer manual setup:

### 1. Prerequisites

- Ubuntu 22.04+ (or any Debian-based system)
- Docker 24+ and Docker Compose v2
- Git
- 2GB+ available disk space

### 2. Clone Repository

```bash
git clone https://github.com/SmartMarstek/GRIP.git
cd GRIP
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
nano .env
```

### 4. Create Required Directories

```bash
mkdir -p data config
mkdir -p grafana/provisioning/dashboards grafana/provisioning/datasources
```

### 5. Start Services

```bash
docker-compose up -d
```

### 6. Access the Application

- **SmartMarstek:** http://localhost
- **Grafana:** http://localhost/grafana (admin / admin)
- **InfluxDB:** http://localhost:8086

## Configuration

### Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `HA_URL` | Home Assistant base URL | _(empty)_ |
| `HA_TOKEN` | Home Assistant API token | _(empty)_ |
| `ENTSOE_API_KEY` | ENTSO-E electricity prices API key | _(empty)_ |
| `ENTSOE_COUNTRY` | ENTSO-E country code (e.g., BE, NL) | BE |
| `TIMEZONE` | Application timezone | Europe/Brussels |
| `INFLUX_USERNAME` | InfluxDB username | marstek |
| `INFLUX_PASSWORD` | InfluxDB password | marstek2024! |
| `INFLUX_ORG` | InfluxDB organization | marstek |
| `INFLUX_BUCKET` | InfluxDB bucket name | energy |
| `INFLUX_ADMIN_TOKEN` | InfluxDB admin token | marstek-influx-token-local |
| `GRAFANA_PASSWORD` | Grafana admin password | admin |
| `LOG_LEVEL` | Log level (trace, debug, info, warning, error) | info |

## Services

### SmartMarstek (Flask App)
- **Container:** `marstek-app`
- **Port:** 5000 (proxied via nginx:80)
- **Volume:** `/data` (configuration and data files)
- **Environment:** `STANDALONE_MODE=true`

### InfluxDB 2.7
- **Container:** `marstek-influxdb`
- **Port:** 8086
- **Volume:** `influxdb_data` (time series data)
- **Init Config:** Auto-initialized with org, bucket, and token

### Grafana 11.0.0
- **Container:** `marstek-grafana`
- **Port:** 3000 (proxied via nginx:80/grafana)
- **Volume:** `grafana_data` (dashboards and settings)
- **Provisioning:** Auto-configured with InfluxDB datasource and 5 dashboards

### Nginx
- **Container:** `marstek-nginx`
- **Ports:** 80, 443
- **Config:** Reverse proxy for app and grafana
- **Features:**
  - Smart routing (/ → app, /grafana → grafana)
  - WebSocket support
  - Static file serving

## Dashboards

Five pre-configured Grafana dashboards are automatically provisioned:

1. **Live Energy Flow** — Real-time solar/battery/grid/house power gauges and 24-hour history
2. **Battery Optimization** — Battery SOC and charging/discharging activity
3. **Cost Savings Analysis** — Daily grid costs and cumulative savings
4. **Solar Forecast Accuracy** — Forecast vs actual power, error metrics
5. **AI Strategy Log** — Claude/ChatGPT decision history and impact

Access dashboards at: **http://localhost/grafana**

## Backup & Restore

### Backup

Create a backup of configuration and InfluxDB data:

```bash
./backup.sh
```

This creates a `.tar.gz` file with:
- `/data` directory (configuration files)
- InfluxDB backup
- `.env` file

### Restore

Restore from a backup file:

```bash
./restore.sh smartmarstek_backup_20240425_103045.tar.gz
```

The script will:
1. Stop containers
2. Restore configuration files
3. Restore InfluxDB data (with manual steps if needed)
4. Restart services

## Docker Compose Commands

```bash
# View logs
docker-compose logs -f smartmarstek

# Stop all services
docker-compose down

# Restart a service
docker-compose restart smartmarstek

# View container status
docker-compose ps

# Execute command in container
docker-compose exec smartmarstek bash

# Pull latest image and restart
docker-compose pull && docker-compose up -d
```

## Networking

The deployment uses a custom bridge network (`marstek-net`) to enable inter-container communication:

- SmartMarstek → InfluxDB: `http://influxdb:8086`
- Grafana → InfluxDB: `http://influxdb:8086`
- Nginx → SmartMarstek: `http://smartmarstek:5000`
- Nginx → Grafana: `http://grafana:3000`

External access is only through Nginx on ports 80 and 443.

## Volumes

| Volume | Container | Purpose |
|--------|-----------|---------|
| `./data` | smartmarstek | Application configuration and data |
| `influxdb_data` | influxdb | InfluxDB time series database |
| `influxdb_config` | influxdb | InfluxDB configuration |
| `grafana_data` | grafana | Grafana dashboards and settings |

## Troubleshooting

### Services won't start

Check logs:
```bash
docker-compose logs
```

Ensure ports 80, 443, 5000, 8086, 3000 are not in use:
```bash
sudo netstat -tulpn | grep -E ':(80|443|3000|5000|8086)'
```

### InfluxDB initialization fails

The first start may take 30+ seconds. Wait and check:
```bash
docker-compose logs influxdb
```

### Grafana dashboards not loading

Verify InfluxDB datasource is healthy:
1. Open http://localhost/grafana
2. Go to **Configuration → Data sources**
3. Check InfluxDB connection

### Can't connect to SmartMarstek

Verify nginx is running:
```bash
docker-compose exec nginx curl http://smartmarstek:5000
```

## Upgrade

To upgrade SmartMarstek to the latest version:

```bash
cd ~/smartmarstek
git pull origin main
docker-compose pull
docker-compose up -d
```

## Uninstall

To completely remove the deployment:

```bash
docker-compose down -v
cd ..
rm -rf smartmarstek
```

> ⚠️ This will delete all data. Create a backup first if needed.

## Additional Resources

- [SmartMarstek GitHub](https://github.com/SmartMarstek/GRIP)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [InfluxDB 2.x Documentation](https://docs.influxdata.com/influxdb/v2.7/)
- [Grafana Documentation](https://grafana.com/docs/)

## Support

For issues or questions:
1. Check the logs: `docker-compose logs`
2. Review the [SmartMarstek GitHub Issues](https://github.com/SmartMarstek/GRIP/issues)
3. Join the community discussions

## Development

If you're modifying SmartMarstek:

1. Make changes to `backend/` or `frontend/` in the repo
2. Rebuild the Docker image:
   ```bash
   docker build -t smartmarstek:dev .
   ```
3. Update `docker-compose.yml` to use your image
4. Restart: `docker-compose up -d`

For local development without Docker, see the main README.md.
