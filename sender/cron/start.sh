#!/bin/bash
> /cron/cronfile
if [ -z "$CADVISOR_URL" ]; then 
	echo "CADVISOR URL is unset" 
else 
	echo "export CADVISOR_URL=$CADVISOR_URL" >> /cron/cronfile 
fi
echo "export COLLECTOR_URL=$COLLECTOR_URL" >> /cron/cronfile
echo "export DOCKER_API_VERSION=$DOCKER_API_VERSION" >> /cron/cronfile
rm -rf /cron/bin/Cron
mkdir /cron/bin/Cron
ln /cron/bin/runcron /cron/bin/Cron/sender.sh
rsyslogd
if [[ $(pgrep cron) ]]; then 
	echo "cron has already been started"
else
	echo "cron has not been started" 
	echo "Starting cron..."
	rm -f /var/run/crond.pid
	cron
fi
tail -f /var/log/syslog /var/log/cron.log