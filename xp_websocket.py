import asyncio
import json
from requests import Session
import websockets

DEFAULT_XPLANE_WS_URL = "ws://localhost:8086/api/v2"
DEFAULT_XPLANE_REST_URL = "http://localhost:8086/api/v2"

class XP_Websocket:
    def __init__(self, rest_url = DEFAULT_XPLANE_REST_URL, ws_url = DEFAULT_XPLANE_WS_URL):
        self.led_dataref_ids = {}  # dict: data_id -> led / ledarray
        self.buttonref_ids = {} # dict: button -> cmd_id
        #self.buttonref_ids = {} # dict: button -> data_id
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.xp = Session()
        self.xp.headers["Accept"] = "application/json"
        self.xp.headers["Content-Type"] = "application/json"
        self.iddict = {}
        self.req_id = 0
        self.ws = None
        self.datacache = {}


    def dataref_id_fetch(self, dataref):
        xpdr_code_response = self.xp.get(self.rest_url + "/datarefs", params={"filter[name]": dataref})
        if xpdr_code_response.status_code != 200:
            print(f"could not get id for {dataref}, Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None
        return xpdr_code_response.json()["data"][0]["id"]
    

    def command_id_fetch(self, command):
        xpdr_code_response = self.xp.get(self.rest_url + "/commands", params={"filter[name]": command})
        if xpdr_code_response.status_code != 200:
            print(f"could not get id for {command}, Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None
        return xpdr_code_response.json()["data"][0]["id"]


    def dataref_set_value(self, id, value, index = None):
        if type(id) is not int:
            id = self.dataref_id_fetch(id)

        set_msg = {
            "data": int(value)
        }
        if index != None:
            xpdr_code_response = self.xp.patch(self.rest_url + "/datarefs/" + str(id) + "/value", data=json.dumps(set_msg), params={"index":index})
        else:
            xpdr_code_response = self.xp.patch(self.rest_url + "/datarefs/" + str(id) + "/value", data=json.dumps(set_msg))   

        if xpdr_code_response.status_code != 200:
            print(f"could not set data for id {id}. Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None


    def command_activate_duration(self, id, duration = 0.2):
        if type(id) is not int:
            id = self.command_id_fetch(id)

        set_msg = {
            "duration": duration
        }

        xpdr_code_response = self.xp.post(self.rest_url + "/command/" + str(id) + "/activate", data=json.dumps(set_msg))

        if xpdr_code_response.status_code != 200:
            print(f"could not send command for id {id}. Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None


    def datarefs_subscribe(self,dataref_list, update_callback = None):
        asyncio.run(self.async_dataref_subscribe(dataref_list, update_callback))


    async def async_dataref_subscribe(self, dataref_list, update_callback):
        await (self.async_dataref_subsrcibe2_listener(dataref_list, update_callback))


    async def async_dataref_subsrcibe2_listener(self, dataref_list, update_callback):
        async with websockets.connect(self.ws_url, open_timeout=100) as ws:
            self.ws = ws
            # Abonnement senden
            subscribe_msg = {
                "req_id": self.req_id,
                "type": "dataref_subscribe_values",
                "params": {
                    "datarefs": [{"id": ref_id} for ref_id in dataref_list]
                    #"commands": [{"id": ref_id_cmd} for ref_id_cmd in buttonref_ids]
                }
            }
            await ws.send(json.dumps(subscribe_msg))
            self.req_id += 1

            # wait for ack
            ack = await ws.recv()
            ack_data = json.loads(ack)
            if not ack_data.get("success", False):
                print(f"Abonnement fehlgeschlagen: {ack_data}")
                return

            # main-rx-Loop: get updates
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                except Exception as e:
                    print(f"[A107] Fehler im Listener: {e}")
                    break
                if update_callback:
                    update_callback(data, dataref_list)