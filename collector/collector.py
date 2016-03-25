__author__ = 'uphoff & grieger'
"""
A python3 stats collector for gathering docker stats metrics and storing them in Influxdb.

It is recommended to run and install this within a virtualenv for testing and development.

Installation:

    pip install -r requirements.txt

Environment variable examples:

    COLLECTOR_PORT=8787
    COLLECTOR_INFLUXDB_HOST=XXX.XXX.XXX.XXX
    COLLECTOR_INFLUXDB_PORT=6379
    COLLECTOR_INFLUXDB_NAME=metrics

Running:

    python collector.py

Running w/gunicorn:

    gunicorn --workers=1 --log-level debug --log-file=- --bind 0.0.0.0:$COLLECTOR_PORT 'collector:build_app()'

"""


import os
import falcon
import time
import json
import logging
import requests
from wsgiref import simple_server
from influxdb import InfluxDBClient
from multiprocessing import Process

PORT=int(os.getenv('COLLECTOR_PORT', '8787'))
STATS_LEN=int(os.getenv('STATS_LEN', '1440'))

INFLUXDB_HOST=os.getenv('COLLECTOR_INFLUXDB_HOST', 'localhost')
INFLUXDB_PORT=int(os.getenv('COLLECTOR_INFLUXDB_PORT', '8086'))
INFLUXDB_NAME=os.getenv('COLLECTOR_INFLUXDB_NAME', 'metrics')
INFLUXDB_ADMIN_USERNAME=os.getenv('INFLUXDB_ADMIN_USERNAME', 'root')
INFLUXDB_ADMIN_PASSWORD=os.getenv('INFLUXDB_ADMIN_PASSWORD', 'root')

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)
logger.addHandler(logging.StreamHandler())


class MetricsCollectorResource(object):

    def __init__(self, influxdb_host, influxdb_port, influxdb_dbname, influxdb_admin_username, 
                 influxdb_admin_password, metadata_fun=None):
        self.logger = logging.getLogger(__name__)
        self.influxdb_port = influxdb_port
        self.influxdb_host = influxdb_host
        self.influxdb_dbname = influxdb_dbname
        self.influxdb_username = influxdb_admin_username
        self.influxdb_password = influxdb_admin_password

        self.metadata_fun = metadata_fun

        self.fmt = lambda obj: json.dumps(obj, indent=4, sort_keys=True) # Handy JSON function


    def on_post(self, req, resp):
        """
        Receives aggregated cAdvisor stats from docker hosts.
        Send them on to a spawned StatHandler to avoid blocking the sender.

        :param req: The HTTP request
        :param resp: The HTTP response
        """

        # Get the IP of the host sending stats
        remote_ip = req.env['REMOTE_ADDR']

        # The cadvisor data is JSON in the request body
        body = req.stream.read()
        if not body:
            self.logger.error('Empty body provided when returning a command result.')
            raise falcon.HTTPBadRequest('Empty request body', 'A valid JSON document is required.')

        entry = json.loads(body.decode())

        # Set up to process the stats in the background so as not to block.
        handler = StatHandler(self.influxdb_host, self.influxdb_port, self.influxdb_dbname,self.influxdb_username, 
                              self.influxdb_password, self.metadata_fun)

        p = Process(target=handler.process, args=(entry, remote_ip,))
        p.start()

        resp.set_header('Content-Type', 'application/json')
        resp.status = falcon.HTTP_200
        resp.body = self.fmt({})

class StatHandler():

    def __init__(self, influxdb_host, influxdb_port, influxdb_dbname, influxdb_admin_username, 
                 influxdb_admin_password, metadata_fun=None):
        """
        Create the handler. Use the optional metadata_fun to gather external data to store with the stats.

        :param rinfluxdb_host: Where to connect to Redis
        :param influxdb_port: Port influxdb is listening on
        :param metadata_fun: A function for grabbing external metadata (see _get_metadata_noop)
        """

        self.logger = logging.getLogger(__name__)
        self.influxdb_port = influxdb_port
        self.influxdb_host = influxdb_host
        self.influxdb_dbname = influxdb_dbname
        self.influxdb_username = influxdb_admin_username
        self.influxdb_password = influxdb_admin_password

        if metadata_fun:
            self._get_metadata = metadata_fun
        else:
            self._get_metadata = self._get_metadata_default

    def process(self, entry, remote_ip):
        """
        Given a stats entry and IP, store the stats and machine data in Redis.
        """

        self.logger.debug(entry) #Causing container disk usage to increase
        influx_client = InfluxDBClient(host=self.influxdb_host, port=self.influxdb_port, username=self.influxdb_username, 
                                       password=self.influxdb_password, database=self.influxdb_dbname, timeout=15)
        self._get_metadata(entry, remote_ip, influx_client, ignore_fail=False)
            

    def _get_metadata_default(self, entry, remote_ip, influx_client, ignore_fail=False):
        """
        Handles collection of ancillary metadata. The default implementation is a no op.
        At Catalyze we use a function here that pulls data from an internal API
        to find related details about the container.
        This function can be adjusted to suit individual needs, although a function can be passed
        into the constructor so long as it has the same parameters as this one.
        :param name: The container name
        :param container: The container data recieved from response
        :param ignore_fail: boolean indicating if we should ignore failures in lookup or not
        :return: True if ignore_fail is True or if the data was looked up successfully, else returns False
        """
        try:
            for container in entry.keys():
                for metric in entry[container]:
                    metric['tags']['remote_ip'] = remote_ip
                influx_client.write_points(entry[container])
            return True
        
        except Exception as e:
            # Could fail to connect altogether
            logger.error(e)
            return ignore_fail


class CollectorApp():

    def build_app(self, metadata_fun=None):
        # Launch background process to clean up stale stats/containers
        # TODO: figure out how to clean up ephemeral container data in influx
        # This funcitonality will likely be replaced by setting up the retention policy for influx manually 

        app = falcon.API()
        resource = MetricsCollectorResource(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_NAME, INFLUXDB_ADMIN_USERNAME, 
                                            INFLUXDB_ADMIN_PASSWORD, metadata_fun)
        app.add_route('/collector/metrics', resource)
        return app

def build_app():
    c = CollectorApp()
    return c.build_app()

if __name__ == '__main__':
    # For testing outside a WSGI like gunicorn
    collector = CollectorApp()
    httpd = simple_server.make_server('0.0.0.0', PORT, collector.build_app())
    httpd.serve_forever()