
import asyncio
import logging

import paho.mqtt.client as mqtt_client
# sudo apt install python3-paho-mqtt
import inspect
import time

from myEvent import myEvent   

logger = logging.getLogger(__name__)

class MqttService:
    def __init__(self, mqttConfig):
        logger.trace(f"mqttConfig: {mqttConfig}")
        self.mqttConfig = mqttConfig
        self.aquaclean_loop = None
        logger.trace(f"self.mqttConfig['topic']: {self.mqttConfig['topic']}")

        self.mqttc = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, self.mqttConfig['client'] + str(int(time.time())))
        self.mqttc.enable_logger()

        self.ToggleLidPosition = myEvent.EventHandler()
        self.Connect           = myEvent.EventHandler()
        self.ToggleAnal        = myEvent.EventHandler()
        self.SetBleConnection  = myEvent.EventHandler()
        self.SetPollInterval   = myEvent.EventHandler()
        self.Disconnect        = myEvent.EventHandler()
        self.ConnectESP32            = myEvent.EventHandler()
        self.DisconnectESP32         = myEvent.EventHandler()
        self.SetEsphomeApiConnection = myEvent.EventHandler()


    async def start_async(self, aquaclean_loop, mqtt_initialized_wait_queue):
        self.aquaclean_loop              = aquaclean_loop
        self.mqtt_initialized_wait_queue = mqtt_initialized_wait_queue
        self.mqttc.on_connect            = self.on_connect
        self.mqttc.on_message            = self.on_message
        self.mqttc.on_subscribe          = self.on_subscribe
        self.mqttc.on_unsubscribe        = self.on_unsubscribe
        self.mqttc.on_publish            = self.on_publish

        self.mqttc.user_data_set([])
        # self.mqttc.username_pw_set(username, password)
        # config.get("MQTT", "user", fallback=None)
        
        logger.trace(f"type(self.mqttConfig): {type(self.mqttConfig)}" )
        logger.trace(f"self.mqttConfig: {self.mqttConfig}" )
        logger.trace(f"self.mqttConfig.__dir__: {self.mqttConfig.__dir__}" )

        logger.trace(f"getattr(self.mqttConfig, 'client', None): {getattr(self.mqttConfig, 'client', None)}" )
        logger.trace(f"getattr(self.mqttConfig, 'Server', None): {getattr(self.mqttConfig, 'server', None)}" )
        logger.trace(f"getattr(self.mqttConfig, 'port', None): {getattr(self.mqttConfig, 'port', None)}" )
        logger.trace(f"getattr(self.mqttConfig, 'Password', None): {getattr(self.mqttConfig, 'Password', None)}" )

        logger.trace(f"self.mqttConfig.get('server', None)): {self.mqttConfig.get('server', None)}" ) # keys are in lower case !!
        logger.trace(f"self.mqttConfig.get('password', None)): {self.mqttConfig.get('password', None)}" )
        
        

        self.mqttc.username_pw_set(self.mqttConfig.get('username', None), self.mqttConfig.get('password', None))

        logger.trace(f"self.aquaclean_loop: {self.aquaclean_loop}")

        try:
            self.mqttc.connect(self.mqttConfig['server'], int(self.mqttConfig['port']), 60)
            self.mqttc.loop_start()
        except Exception as ex:
            logging.error(f"### CONNECTING FAILED ### {ex}")

    def on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        # Since we subscribed only for a single channel, reason_code_list contains
        # a single entry
        if reason_code_list[0].is_failure:
            logger.info("Broker rejected you subscription: {reason_code_list[0]}")
        else:
            logger.info(f"Broker granted the following QoS: {reason_code_list[0].value}")
            logger.trace("subscription properties: %s", properties)
            logger.trace("self.mqtt_initialized_wait_queue.put('on_subscribe without errors')")
            self.mqtt_initialized_wait_queue.put("on_subscribe without errors")

    def on_unsubscribe(self, client, userdata, mid, reason_code_list, properties):
        # Be careful, the reason_code_list is only present in MQTTv5.
        # In MQTTv3 it will always be empty
        if len(reason_code_list) == 0 or not reason_code_list[0].is_failure:
            print("unsubscribe succeeded (if SUBACK is received in MQTTv3 it success)")
        else:
            logger.info(f"Broker replied with failure: {reason_code_list[0]}")
        client.disconnect()

    def on_disconnect(self, client, userdata, rc):
        logger.info("### DISCONNECTED ###")
        if self.aquaclean_loop and self.aquaclean_loop.is_running():
            asyncio.run_coroutine_threadsafe(self.reconnect(), self.aquaclean_loop)

    async def reconnect(self):
        try:
            self.mqttc.reconnect()
            logger.info("MQTT reconnected")
        except Exception as e:
            logger.warning(f"MQTT reconnect failed: {e}")

    def on_connect(self, client, userdata, flags, reason_code, properties):
        logger.trace("mqtt, on_connect, reason_code: %s", reason_code)
        logger.trace("mqtt, on_connect, flags: %s", flags)
        logger.trace("mqtt, on_connect, userdata: %s", userdata)
        logger.trace("mqtt, on_connect, client: %s", client)
        logger.trace("mqtt, on_connect, properties: %s", properties)
        logger.info("### CONNECTED WITH SERVER ###")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleLidPosition")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleAnal")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/centralDevice/control/connect")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/centralDevice/control/disconnect")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/centralDevice/config/bleConnection")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/centralDevice/config/pollInterval")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/esphomeProxy/control/connect")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/esphomeProxy/control/disconnect")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/esphomeProxy/config/apiConnection")
        logger.info("### SUBSCRIBED ###")

    def on_message(self, client, userdata, msg):
        logger.info("### RECEIVED APPLICATION MESSAGE ###")
        logger.trace(f"+ Topic = {msg.topic}")
        logger.trace(f"+ Payload = {msg.payload.decode()}")
        logger.trace(f"+ QoS = {msg.qos}")
        logger.trace(f"+ Retain = {msg.retain}")

        if msg.topic == f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleLidPosition":
            self.handle_toggleLidPositionMessage()
        elif msg.topic == f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleAnal":
            self.handle_toggle_anal_message()
        elif msg.topic == f"{self.mqttConfig['topic']}/centralDevice/control/connect":
            self.handle_connect_message()
        elif msg.topic == f"{self.mqttConfig['topic']}/centralDevice/control/disconnect":
            self.handle_disconnect_message()
        elif msg.topic == f"{self.mqttConfig['topic']}/centralDevice/config/bleConnection":
            self.handle_set_ble_connection_message(msg.payload.decode().strip())
        elif msg.topic == f"{self.mqttConfig['topic']}/centralDevice/config/pollInterval":
            self.handle_set_poll_interval_message(msg.payload.decode().strip())
        elif msg.topic == f"{self.mqttConfig['topic']}/esphomeProxy/control/connect":
            self.handle_esp32_connect_message()
        elif msg.topic == f"{self.mqttConfig['topic']}/esphomeProxy/control/disconnect":
            self.handle_esp32_disconnect_message()
        elif msg.topic == f"{self.mqttConfig['topic']}/esphomeProxy/config/apiConnection":
            self.handle_set_esphome_api_connection_message(msg.payload.decode().strip())


    def handle_toggleLidPositionMessage(self):
        logger.trace("in handle_toggleLidPositionMessage")

        for handler in self.ToggleLidPosition.get_handlers():
            # https://stackoverflow.com/questions/57329801/python-asyncio-runtimeerror-non-thread-safe-operation-invoked-on-an-event-loop
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_connect_message(self):
        logger.trace("in handle_connect_message")
        for handler in self.Connect.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_toggle_anal_message(self):
        logger.trace("in handle_toggle_anal_message")
        for handler in self.ToggleAnal.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_disconnect_message(self):
        logger.trace("in handle_disconnect_message")
        for handler in self.Disconnect.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_set_ble_connection_message(self, value: str):
        logger.trace(f"in handle_set_ble_connection_message: {value!r}")
        for handler in self.SetBleConnection.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(value), self.aquaclean_loop)
            _ = future.result()

    def handle_esp32_connect_message(self):
        logger.trace("in handle_esp32_connect_message")
        for handler in self.ConnectESP32.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_esp32_disconnect_message(self):
        logger.trace("in handle_esp32_disconnect_message")
        for handler in self.DisconnectESP32.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()

    def handle_set_esphome_api_connection_message(self, value: str):
        logger.trace(f"in handle_set_esphome_api_connection_message: {value!r}")
        for handler in self.SetEsphomeApiConnection.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(value), self.aquaclean_loop)
            _ = future.result()

    def handle_set_poll_interval_message(self, value: str):
        logger.trace(f"in handle_set_poll_interval_message: {value!r}")
        try:
            interval = float(value)
        except ValueError:
            logger.warning(f"Invalid poll interval from MQTT: {value!r}")
            return
        for handler in self.SetPollInterval.get_handlers():
            future = asyncio.run_coroutine_threadsafe(handler(interval), self.aquaclean_loop)
            _ = future.result()


    def stop(self):
        """Stop the MQTT network loop and disconnect from the broker."""
        try:
            self.mqttc.loop_stop()
            self.mqttc.disconnect()
        except Exception as ex:
            logger.debug(f"MQTT stop: {ex}")
        logger.info("### MQTT STOPPED ###")

    async def send_data_async(self, topic, value):
        logger.trace("send_data_async...")
        logger.trace(f"topic: {topic}, value: {value}")

        try:
            self.mqttc.publish( topic, value, retain=True)
        except Exception as ex:
            logging.error(f"### SENDING DATA FAILED ### {ex}")

    def on_publish(self, client, userdata, mid, reason_code, properties):
        # reason_code and properties will only be present in MQTTv5. It's always unset in MQTTv3
        logger.trace("Publishing...")


