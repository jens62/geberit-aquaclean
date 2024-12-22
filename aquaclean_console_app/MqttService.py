
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


    async def start_async(self, aquaclean_loop):
        self.aquaclean_loop       = aquaclean_loop
        self.mqttc.on_connect     = self.on_connect
        self.mqttc.on_message     = self.on_message
        self.mqttc.on_subscribe   = self.on_subscribe
        self.mqttc.on_unsubscribe = self.on_unsubscribe
        self.mqttc.on_publish     = self.on_publish

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
        asyncio.create_task(self.reconnect())

    def on_connect(self, client, userdata, flags, reason_code, properties):
        logger.trace("mqtt, on_connect, reason_code: %s", reason_code)
        logger.trace("mqtt, on_connect, flags: %s", flags)
        logger.trace("mqtt, on_connect, userdata: %s", userdata)
        logger.trace("mqtt, on_connect, client: %s", client)
        logger.trace("mqtt, on_connect, properties: %s", properties)
        logger.info("### CONNECTED WITH SERVER ###")
        self.mqttc.subscribe(f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleLidPosition")
        logger.info("### SUBSCRIBED ###")

    def on_message(self, client, userdata, msg):
        logger.info("### RECEIVED APPLICATION MESSAGE ###")
        logger.trace(f"+ Topic = {msg.topic}")
        logger.trace(f"+ Payload = {msg.payload.decode()}")
        logger.trace(f"+ QoS = {msg.qos}")
        logger.trace(f"+ Retain = {msg.retain}")

        if msg.topic == f"{self.mqttConfig['topic']}/peripheralDevice/control/toggleLidPosition":
            self.handle_toggleLidPositionMessage()


    def handle_toggleLidPositionMessage(self):
        logger.trace("in handle_toggleLidPositionMessage")
        
        for handler in self.ToggleLidPosition.get_handlers():
            # https://stackoverflow.com/questions/57329801/python-asyncio-runtimeerror-non-thread-safe-operation-invoked-on-an-event-loop
            future = asyncio.run_coroutine_threadsafe(handler(), self.aquaclean_loop)
            _ = future.result()


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


