# TitanFlow v0.3 Deploy Package (Sarge)

## 1) Sync to Sarge
```bash
rsync -av --delete ./TitanFlow/ kamaldatta@10.0.0.33:/opt/titanflow/
```

## 2) Install systemd units
```bash
sudo cp /opt/titanflow/deploy/systemd/titanflow-v03.service /etc/systemd/system/
sudo cp /opt/titanflow/deploy/systemd/titanflow-v03-telemetry-http.service /etc/systemd/system/
sudo cp /opt/titanflow/deploy/systemd/titanflow-v03-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 3) Enable + start services
```bash
sudo systemctl enable --now titanflow-v03.service
sudo systemctl enable --now titanflow-v03-telemetry-http.service
sudo systemctl enable --now titanflow-v03-gateway.service
```

## 4) Verify
```bash
systemctl status titanflow-v03.service
curl http://10.0.0.33:19100/status
curl http://10.0.0.33:18888/health
```

## 5) Logs
```bash
journalctl -u titanflow-v03.service -f
journalctl -u titanflow-v03-telemetry-http.service -f
journalctl -u titanflow-v03-gateway.service -f
```
