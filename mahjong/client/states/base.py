import select
import socket

from mahjong.packets import *
from mahjong.poll import *
from mahjong.shared import *

if TYPE_CHECKING:
  from mahjong.client import Client


class ClientState:
  def __init__(self, client: 'Client'):
    self.client = client

  @property
  def poll(self):
    return self.client.poll

  @property
  def state(self):
    return self.client.state

  @state.setter
  def state(self, state: 'ClientState'):
    self.client.state = state

  def on_server_data(self, server: socket.socket, event: int):
    if event & select.POLLHUP:
      self.on_server_disconnect(server)
    elif event & select.POLLIN:
      self.on_server_packet(server, self.read_packet())

  def on_server_disconnect(self, server: socket.socket):
    self.poll.unregister(server)
    server.close()

  def on_server_packet(self, server: socket.socket, packet: Packet):
    pass

  def on_input(self, input: str):
    pass

  def read_packet(self):
    return read_packet(self.client.socket)

  def send_packet(self, packet: Packet):
    send_packet(self.client.socket, packet)