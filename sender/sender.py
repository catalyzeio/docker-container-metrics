from __future__ import print_function

"""
This script is intended to be run via cron every minute.
It gathers the last minute of cadvisor stats, rolls them up and then
uploads stats to the collector endpoint.

For this script to run, cadvisor (https://github.com/google/cadvisor) must be running
along with the collector (see collector/collector.py).

Environment variable examples:

    # The URL for the cadvisor API
    CADVISOR_URL=http://127.0.0.1:8989/api/v1.2

    # The URL for the collector endpoint
    COLLECTOR_URL=http://127.0.0.1:8787/cadvisor/metrics

    # The Docker remote api version
    DOCKER_API_VERSION=1.20

Running:

    python sender.py

"""
import os
import time
import json
import requests
import uuid
import dateutil.parser
from docker import Client
from multiprocessing import Process, Manager

# Determine the collector URL. The default collector.local address is used to make running via docker easier.
endpoint = os.getenv('COLLECTOR_URL', 'http://0.0.0.0:8989/collector/metrics/')

# Determine the docker remote api version.
docker_api_version = os.getenv('DOCKER_API_VERSION', None)

# Check if cadvisor is 
cadvisor_base = os.getenv('CADVISOR_URL', None)
docker_client = Client(base_url='unix://var/run/docker.sock', version=docker_api_version)

# Check if cadvisor is running on this host
if cadvisor_base is None:
    for container in docker_client.containers():
        if 'cadvisor' in str(container):
            port = container['Ports'][0]['PublicPort']
            ip = docker_client.inspect_container(container['Id'])['NetworkSettings']['Gateway']
            cadvisor_base = 'http://%s:%s/api/v1.2' % (ip, port)
            

def match_all_but_sender(name, value):
    """
    Match on anything that isn't cadvisor.
    """
    if 'sender' in value['aliases']:
        return False
    return True


def match_all(name, value):
    """
    Match on all containers, including cadvisor.
    """
    return True


def match_on_uuid(name, value):
    """
    Match on any container that has a UUID as its name.
    This is used internally at Catalyze, as all of our customer containers have a UUID as their container name.
    """
    if 'sender' in value['aliases']:
        return False  # Ignore cadvisor
    try:
        # Try to parse as a UUID and match if it works
        uuid.UUID(name, version=1)
        return True
    except ValueError:
        # Not a UUID so not a container to match on
        return False

match_type_name = os.getenv('MATCH_TYPE', 'ALL')
if match_type_name == 'ALL':
    match_container_name = match_all 
elif match_type_name == 'UUID':
    match_container_name = match_on_uuid
elif match_type_name == 'NO_CADVISOR':
    match_container_name = match_all_but_cadvisor
    

def total_min_max(stat, s_total, s_min, s_max):
    """
    Given a value (stat), add it to the total and check if it is the new
    min value or max value compared to s_min and s_max.
    """
    result_min = s_min
    result_max = s_max
    s_total += stat
    if s_min == None or stat < s_min:
        result_min = stat
    if s_max == None or stat > s_max:
        result_max = stat
    return s_total, result_min, result_max


def process_diskio(diskio, field):
    """
    Sum up all the disk IO bytes for a given field (Sync, Async, Read, Write).
    Only considering io_service_bytes stats right now (io_serviced is ignored).
    """
    total = 0
    io_stats = diskio['io_service_bytes']
    for entry in io_stats:
        total += entry['stats'][field]
    return total


def get_stats_from_daemon(stats_obj, container_id, container_aliases, machine_stats, interval):
    """
    Collects metrics for 1 minute from the docker remote api
    """
    end_time = time.time() + interval
    collected_stats = []
    try:
        for container_stat in stats_obj:
            all_stats = json.loads(container_stat)

            # Select only the stats that you want to collect to conserve memory
            wanted_metrics = ['read', 'memory_stats', 'cpu_stats', 'network']
            stats = {k: all_stats[k] for k in set(wanted_metrics) & set(all_stats.keys())}
            collected_stats.append(stats)
            if len(collected_stats) >= interval or time.time() >= end_time:
                # Delete docker stats object to conserve memory
                del stats_obj
                break
    except:
        collected_stats = None
    finally:
        machine_stats[container_id] = {'aliases': container_aliases, 'stats': collected_stats}       


def get_dockerstats_metrics(endpoint, docker_client):
    """
    Pull performance metrics from docker remote api
    """

    interval = 60
    machine_stats = Manager().dict()
    processes = []
    payload = {}

    # Set up processes to collect stats from docker dameon for each container
    for container in docker_client.containers():
        processes.append(Process(target=get_stats_from_daemon, 
            args=(docker_client.stats(container['Id']),container['Id'],
                  container['Names'], machine_stats, interval)))
    for p in processes:
        p.start()
        p.join()

    for key, value in machine_stats.items():

        # Determine if one of the aliases matches (is something we want to collect metrics for)
        container_name = None
        for name in value['aliases']:
            if match_container_name(name.replace('/',''), value):
                container_name = name.replace('/','')
                break

        # Skip this if the container didn't match
        if container_name == None:
            continue

        # Compute the timestamp, using the first second in this series
        ts = int(dateutil.parser.parse(value['stats'][0]['read']).strftime('%s'))

        # Run through all the stat entries for this container
        stats = value['stats']
        stats_len = len(stats)  # Should always be 60

        # Initialize min/max/total variables for memory, cpu
        total_memory = 0
        min_memory = None
        max_memory = None
        total_load = 0
        min_load = None
        max_load = None

        # Compute min, max and average for all non-cumulative stats
        for stat in stats:

            # Grab the memory usage stats
            memory = stat['memory_stats']
            memory_kb = memory['usage']/1024.0
            total_memory, min_memory, max_memory = total_min_max(memory_kb, total_memory, min_memory, max_memory)

            # Get the CPU load. The load value is always 0?
            cpu = stat['cpu_stats']
            cpu_load = cpu['cpu_usage']['total_usage']
            total_load, min_load, max_load = total_min_max(cpu_load, total_load, min_load, max_load)

        # Compute first/last values of cumulative counters
        first = stats[0]  # First item in this series
        last = stats[stats_len-1]  # Last item in this series
        system_cpu = last['cpu_stats']['system_cpu_usage'] - first['cpu_stats']['system_cpu_usage'] # total system cpu usage
        
        cpu_usage = {
                   'total': last['cpu_stats']['cpu_usage']['total_usage'] - first['cpu_stats']['cpu_usage']['total_usage'], 
                    }
        memory_usage = {
                    'total': total_memory,
                    'ave': total_memory/stats_len,
                    'max': max_memory,
                    'min': min_memory
                    }
        network_usage = {
                    'tx_kb': (last['network']['tx_bytes'] - first['network']['tx_bytes'])/1024.0,
                    'rx_kb': (last['network']['rx_bytes'] - first['network']['rx_bytes'])/1024.0,
                    'tx_packets': last['network']['tx_packets'] - first['network']['tx_packets'],
                    'rx_packets': last['network']['rx_packets'] - first['network']['rx_packets'],
                    'tx_errors': last['network']['tx_errors'] - first['network']['tx_errors'],
                    'rx_errors': last['network']['rx_errors'] - first['network']['rx_errors'],
                    'tx_dropped': last['network']['tx_dropped'] - first['network']['tx_dropped'],
                    'rx_dropped': last['network']['rx_dropped'] - first['network']['rx_dropped']
                    }
        payload[container_name] = [format_data('cpu.usage', container_name, ts, cpu_usage),
                                   format_data('memory.usage', container_name, ts, memory_usage),
                                   format_data('network.usage', container_name, ts, network_usage)]
    send(endpoint, payload)


def get_cadvisor_metrics(endpoint, cadvisor_base):
    """
    Pull docker performance metrics from cadvisor
    """

    # Connect to cadvisor and get the last minute's worth of stats (should be 60 stats per container)
    r = requests.get('%s/docker' % cadvisor_base)
    payload = {}
    total_cpu = 0
    #calculate total system cpu usage
    for key,value in r.json().items():
        total_cpu = total_cpu + (value['stats'][-1]['cpu']['usage']['total'] - value['stats'][0]['cpu']['usage']['total']) 

    for key, value in r.json().items():

        # Determine if one of the aliases matches (is something we want to collect metrics for)
        container_name = None
        for name in value['aliases']:
            if match_container_name(name, value):
                container_name = name
                break

        # Skip this if the container didn't match
        if container_name == None:
            continue

        # Compute the timestamp, using the first second in this series
        ts = int(dateutil.parser.parse(value['stats'][0]['timestamp']).strftime('%s'))
        
        # Run through all the stat entries for this container
        stats = value['stats']
        stats_len = len(stats)  # Should always be 60

        # Initialize min/max/total variables for memory, cpu
        total_memory = 0
        min_memory = None
        max_memory = None
        total_load = 0
        min_load = None
        max_load = None
        
        
        # Compute min, max and average for all non-cumulative stats
        for stat in stats:

            # Grab the memory usage stats
            memory = stat['memory']
            memory_kb = memory['usage']/1024.0
            total_memory, min_memory, max_memory = total_min_max(memory_kb, total_memory, min_memory, max_memory)
        
            # Get the CPU load. The load value is always 0?
            cpu = stat['cpu']
            cpu_load = cpu['load_average']
            total_load, min_load, max_load = total_min_max(cpu_load, total_load, min_load, max_load)

        first = stats[0]  # First item in this series
        last = stats[stats_len-1]

        cpu_usage = {
                   'total': last['cpu']['usage']['total'] - first['cpu']['usage']['total'],
                   # TODO: add CPU usage percentage

                   # CPU load metrics available through cadvisor but not available through docker stats
                   # 'ave': total_load/stats_len,
                   # 'max': max_load,
                   # 'min': min_load
                    }
        memory_usage = {
                    'total': total_memory,
                    'ave': total_memory/stats_len,
                    'max': max_memory,
                    'min': min_memory
                    }
        network_usage = {
                    'tx_kb': (last['network']['tx_bytes'] - first['network']['tx_bytes'])/1024.0,
                    'rx_kb': (last['network']['rx_bytes'] - first['network']['rx_bytes'])/1024.0,
                    'tx_packets': last['network']['tx_packets'] - first['network']['tx_packets'],
                    'rx_packets': last['network']['rx_packets'] - first['network']['rx_packets'],
                    'tx_errors': last['network']['tx_errors'] - first['network']['tx_errors'],
                    'rx_errors': last['network']['rx_errors'] - first['network']['rx_errors'],
                    'tx_dropped': last['network']['tx_dropped'] - first['network']['tx_dropped'],
                    'rx_dropped': last['network']['rx_dropped'] - first['network']['rx_dropped']
                }

        payload[container_name] = [format_data('cpu.usage', container_name, ts, cpu_usage),
                                   format_data('memory.usage', container_name, ts, memory_usage),
                                   format_data('network.usage', container_name, ts, network_usage)]

    send(endpoint, payload)


def format_data(measurement_name, container, timestamp, fields):
    """
    Format data to be easily added to influxdb by the collector
    """
    return {
                'measurement': measurement_name,
                'timestamp' : timestamp,
                'tags' :{
                    'container_name': container
                },
                'fields' : fields
           }


def send(endpoint, data):
    """
    POST metrics data to the collector
    """
    headers = {'content-type': 'application/json'}
    post_result = requests.post(endpoint, data=json.dumps(data), headers=headers, timeout=10)
    post_result.raise_for_status()
    print('SENT DATA TO COLLECTOR: %s' % endpoint)


if cadvisor_base is not None:
    # print('Starting cadvisor sender')
    get_cadvisor_metrics(endpoint, cadvisor_base)
else:
    # print('Starting docker stats sender')
    get_dockerstats_metrics(endpoint, docker_client)