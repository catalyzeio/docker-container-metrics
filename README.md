# Catalyze Docker Container Metrics

Centralized collection of metrics for PaaS jobs/services. Each docker host runs a sender process (see the sender directory) that sends docker stats every minute to a central collection point. The collection point, collector, is a small Falcon API that reads in metrics and stores them in Influxdb. 

# Building

Builds are handled by [drone](http://build01.paas.catalyze.io/github.com/catalyzeio/cadvisor-metrics). Two containers are built: collector and sender. 

To run the components locally, check sender/sender.py and collector/collector.py for instructions. 

# Running Collector

First build the collector if not available locally:

    sudo docker build -t collector ./collector

Then run it (adjust IPs and ports) with:

    sudo docker run --name=collector \
        --restart=on-failure:5 \
        -e "COLLECTOR_PORT=8989" \
        -e "COLLECTOR_INFLUXDB_NAME=metrics" \
        -e "COLLECTOR_INFLUXDB_HOST=localhost" \
        -e "COLLECTOR_INFLUXDB_PORT=8086" \
        -e "INFLUXDB_ADMIN_USERNAME=root" \
        -e "INFLUXDB_ADMIN_PASSWORD=root" \
        -p 8989:8989 \
        -t collector

If you have [docker compose](https://docs.docker.com/compose/) installed, edit /collector/docker-compose.yml accordingly and run:

    docker-compose up

# Building Sender

First build the sender with:

    sudo docker build -t sender ./sender

# Running Sender

## Without Cadvisor 
For docker versions >= 1.6 < 1.9

* Note: support for docker versions > 1.9 coming soon

Then run it (adjust IPs and ports) with:

    sudo docker run --name=sender \
        --restart=on-failure:5 \
        -e "DOCKER_API_VERSION=1.20" \
        -e "COLLECTOR_URL=http://0.0.0.0:8989/collector/metrics/" \
        --volume=/var/run:/var/run:rw \
        -t sender 

A table of docker versions and docker remote api versions can be found [here](https://docs.docker.com/engine/reference/api/docker_remote_api/).

If you have [docker compose](https://docs.docker.com/compose/) installed, edit /sender/docker-compose.yml accordingly and run:

    docker-compose up

## With Cadvisor 
For docker versions < 1.6

### Running cadvisor

To run cadvisor, refer to the instructions in the [GitHub repo](https://github.com/google/cadvisor). The current versions of sender and collector work with cadvisor 0.9.0.

Then run it (adjust IPs and ports) with:
    
    sudo docker run --name=sender \
        --restart=on-failure:5 \
        -e "CADVISOR_URL=http://localhost:8080/api/v1.2/" \
        -e "COLLECTOR_URL=http://0.0.0.0:8989/collector/metrics/" \
        -t sender

## Accessing Metrics

An influxdb instance is used to store metrics.  Currently only influxdb version 0.9 is supported by the metrics collector. Also, a metrics database and influx user need to be created in influxdb and the environment variables; COLLECTOR_INFLUXDB_NAME, INFLUXDB_ADMIN_USERNAME and INFLUXDB_ADMIN_PASSWORD need to be defined accordingly.
    
## Metrics Format

Metrics data is stored in three meatsurements in influxdb.

* **network.usage**: stats for network bytes and packets in an out of the container
* **memory.usage**: memory usage in KB
* **cpu.usage**: cpu usage from the start of the minute to the end

Each entry is timestamped and tagged with the following tags.

* **name**: the name of the container, a string
* **remote_ip**: the remote ip of the container, a string

### Network data:
* Each **network.usage** section contains **tx_bytes**, **rx_bytes**, **tx_errors**, **rx_errors**, **tx_packets** and **rx_packets**

### Memory data:
* The **memory.usage** measurement contains **ave** (average), **min** (minimum) and **max** (maximum) fields
* All values are in KB

### CPU data:
* The **cpu.usage** section containes the field **total** the total number of jiffies from the beginining to the end of that minute. CPU usage percentage will be added in the future.

Here is an example entry:
 
```
{
    "container-01" : [
        {
            "measurement": "cpu.usage",
            "timestamp" : "2015-11-11 18:48:05.123",
            "tags" :{
                "name":  "container-01",
                "remote_ip" : "192.168.99.100"
            },
            "fields" : {
            "total": 123456789
            }
        },
        {
            "measurement": "memory.usage",
            "timestamp" : "2015-11-11 18:48:05.123",
            "tags" :{
                "name": "container-01",
                "remote_ip" : "192.168.99.100"
            },
            "fields" : {
                "ave": 0,
                "max": 0,
                "min": 0
            }
        },
        {
            "measurement": "network.usage",
            "timestamp" : "2015-11-11 18:48:05.123",
            "tags" :{
                "name": "container-01",
                "remote_ip" : "192.168.99.100"
            },
            "fields" :{
                "tx_kb": 0,
                "rx_kb": 0,
                "tx_packets": 0,
                "rx_packets": 0,
                "tx_errors": 0,
                "rx_errors": 0,
                "tx_dropped": 0,
                "rx_dropped": 0
            }
        }
    ]
}
```

## Post-processing

We run a number of scripts against the collected metrics to monitor customer applications and gain insights into potential load problems.
In the future we will be providing an example script that aggregates metrics on a per-IP basis. 
This script is best run periodically via cron and then analyzed with something like [pandas](http://pandas.pydata.org/).
Also you can use the Influxdb front end exposed on port 8083 to run queries such as 
    
    select * from "cpu.usage" limit 20

to view the CPU usage data for the latest 20 metrics entries.  
Also, visualization tools such as [grafana](http://grafana.org/) can be easily configured to work with influxdb.

# Contributing

If you find something wrong with our code please submit a GitHub issue or, better yet, submit a pull request. 
For other inquiries please email support@catalyze.io.   

