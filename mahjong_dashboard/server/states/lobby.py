import socket
from typing import TYPE_CHECKING

from mahjong_dashboard.packets import LobbyPlayersServerPacket, send_msg
from mahjong_dashboard.shared import Address
from mahjong_dashboard.wind import Wind

from .base import ServerState
from .game_setup import GameSetupServerState

if TYPE_CHECKING:
  from mahjong_dashboard.server import Server


class LobbyServerState(ServerState):
  def __init__(self, server: 'Server'):
    self.server = server
    self.send_lobby_count()

  def on_client_connect(self, client: socket.socket, address: Address):
    super().on_client_connect(client, address)

    self.send_lobby_count()

    if len(self.clients) == len(Wind):
      self.state = GameSetupServerState(self.server)

  def on_client_disconnect(self, client: socket.socket):
    super().on_client_disconnect(client)

    self.send_lobby_count()

  def send_lobby_count(self):
    packet = LobbyPlayersServerPacket(len(self.clients), len(Wind)).pack()
    for client in self.clients:
      send_msg(client, packet)
