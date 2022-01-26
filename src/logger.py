import collections
import threading
import traceback
import json

from subprocess import PIPE, Popen, check_output

import paho.mqtt.client as mqtt

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559

from bme280 import BME280
from enviroplus import gas
from pms5003 import PMS5003


class EnvLogger:
    def __init__(self, client_id, host, port, username, password, prefix, use_pms5003, num_samples, retain):

        self.bme280 = BME280()

        self.client_id = client_id
        self.prefix = prefix
        self.room = room

        self.connection_error = None
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self.__on_connect
        self.client.username_pw_set(username, password)
        self.client.connect(host, port)
        self.client.loop_start()

        self.samples = collections.deque(maxlen=num_samples)
        self.latest_pms_readings = {}

        self.use_pms5003 = use_pms5003

        if self.use_pms5003:
            self.pm_thread = threading.Thread(
                target=self.__read_pms_continuously)
            self.pm_thread.daemon = True
            self.pm_thread.start()

        self.retain = retain
    

    def __on_connect(self, client, userdata, flags, rc):
        errors = {
            1: "incorrect MQTT protocol version",
            2: "invalid MQTT client identifier",
            3: "server unavailable",
            4: "bad username or password",
            5: "connection refused"
        }

        if rc > 0:
            self.connection_error = errors.get(rc, "unknown error")

    def __read_pms_continuously(self):
        """Continuously reads from the PMS5003 sensor and stores the most recent values
        in `self.latest_pms_readings` as they become available.

        If the sensor is not polled continously then readings are buffered on the PMS5003,
        and over time a significant delay is introduced between changes in PM levels and
        the corresponding change in reported levels."""

        pms = PMS5003()
        while True:
            try:
                pm_data = pms.read()
                self.latest_pms_readings = {
                    "pm10": pm_data.pm_ug_per_m3(
                        1.0),  #, atmospheric_environment=True),
                    "pm25": pm_data.pm_ug_per_m3(
                        2.5),  #, atmospheric_environment=True),
                    "pm100": pm_data.pm_ug_per_m3(
                        10),  #, atmospheric_environment=True),
                }
            except:
                print("Failed to read from PMS5003. Resetting sensor.")
                traceback.print_exc()
                pms.reset()

    def remove_sensor_config(self):
        """
        Remove previous config topic cretead for each sensor
        """
        print("removed")
        sensors = [
            "proximity",
            "lux",
            "temperature",
            "pressure",
            "humidity",
            "oxidising",
            "reducing",
            "nh3",
            "pm10",
            "pm25",
            "pm100",
        ]

        for sensor in sensors:
            sensor_topic_config = f"sensor/{self.room}/{sensor}/config"
            self.publish(sensor_topic_config, '')

    def sensor_config(self):
        """
        Create config topic for each sensor
        """
        # homeassistant/sensor/livingRoom/temperature/config
        # homeassistant/sensor/livingRoom/temperature/state
        # homeassistant/livingroom/enviroplus/state
        sensors = {
            "proximity": {
                "unit_of_measurement": "cm",
                "value_template": "{{ value_json }}"
            },
            "lux": {
                "device_class": "illuminance",
                "unit_of_measurement": "lx",
                "value_template": "{{ value_json }}",
                "icon": "mdi:weather-sunny"
            },
            "temperature": {
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thermometer"
            },
            "pressure": {
                "device_class": "pressure",
                "unit_of_measurement": "hPa",
                "value_template": "{{ value_json }}",
                "icon": "mdi:arrow-down-bold"
            },
            "humidity": {
                "device_class": "humidity",
                "unit_of_measurement": "%H",
                "value_template": "{{ value_json }}",
                "icon": "mdi:water-percent"
            },
            "oxidising": {
                "unit_of_measurement": "no2",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble"
            },
            "reducing": {
                "unit_of_measurement": "CO",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble"
            },
            "nh3": {
                "unit_of_measurement": "nh3",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble"
            },
        }

        if self.use_pms5003:
            sensors["pm10"] = {
                "unit_of_measurement": "ug/m3",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble-outline",
            }
            sensors["pm25"] = {
                "unit_of_measurement": "ug/m3",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble-outline",
            }
            sensors["pm100"] = {
                "unit_of_measurement": "ug/m3",
                "value_template": "{{ value_json }}",
                "icon": "mdi:thought-bubble-outline",
            }
        try:
            for sensor in sensors:
                sensors[sensor]["name"] = f"{self.room} {sensor.capitalize()}"
                sensors[sensor][
                    "state_topic"] = f"{self.prefix}/sensor/{self.room}/{sensor}/state"
                sensors[sensor]["unique_id"] = f"{sensor}-{self.client_id}"

                sensor_topic_config = f"sensor/{self.room}/{sensor}/config"
                self.publish(sensor_topic_config, json.dumps(sensors[sensor]))
            print("Configs added")
        except:
            print("Failed to add configs.")
            traceback.print_exc()

    # Get CPU temperature to use for compensation
    def get_cpu_temperature(self):
        process = Popen(["vcgencmd", "measure_temp"],
                        stdout=PIPE,
                        universal_newlines=True)
        output, _error = process.communicate()
        return float(output[output.index("=") + 1:output.rindex("'")])

    def take_readings(self):
        hum_comp_factor = 1.3
        readings = {}

        try:
            readings["proximity"] = ltr559.get_proximity()
        except OSError:
            print("Error reading proximity sensor data")

        try:
            readings["lux"] = ltr559.get_lux()
        except OSError:
            print("Error reading lux sensor data")

        try:
            readings["temperature"] = self.bme280.get_temperature()
        except OSError:
            print("Error reading temperature sensor data")

        try:
            readings["pressure"] = round(int(self.bme280.get_pressure() * 100), -1)
        except OSError:
            print("Error reading pressure sensor data")

        try:
            readings["humidity"] = round(int(self.bme280.get_humidity() * hum_comp_factor), 1)
        except OSError:
            print("Error reading humidity sensor data")

        try:
            gas_data = gas.read_all()
            readings["oxidising"] = int(gas_data.oxidising / 1000),
            readings["reducing"] = int(gas_data.reducing / 1000),
            readings["nh3"] = int(gas_data.nh3 / 1000),
        except OSError:
            print("Error reading gas sensor data")

        readings.update(self.latest_pms_readings)

        return readings

    def publish(self, topic, value, retain):
        topic = self.prefix.strip("/") + "/" + topic
        self.client.publish(topic, str(value), retain=retain)

    def update(self, publish_readings=True):
        self.samples.append(self.take_readings())
        if publish_readings:
            for topic in self.samples[0].keys():
                try:
                    value_sum = sum([d[topic] for d in self.samples])
                    value_avg = round(value_sum / len(self.samples), 1)
                    self.publish(f"sensor/{self.room}/{topic}/state", value_avg, retain=self.retain)
                except KeyError:
                    print(f"Error publishing data for {topic}")


    def destroy(self):
        self.client.disconnect()
        self.client.loop_stop()
