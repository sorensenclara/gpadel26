# scoreboard/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from .models import Match

class MatchConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.code = self.scope['url_route']['kwargs']['code']
        self.group_name = f"match_{self.code}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Enviamos estado inicial apenas conecta (debug + UX)
        try:
            match = await sync_to_async(Match.objects.get)(code=self.code)
            await self.send(text_data=json.dumps(match.as_payload()))
        except Match.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "match_not_found"}))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # no recibimos comandos del cliente en este demo
        pass

    async def match_update(self, event):
        # Broadcast desde la vista -> cliente
        await self.send(text_data=json.dumps(event["payload"]))
