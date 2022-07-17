import select
import socket
from typing import Dict, Tuple

from mahjong.client import Client
from mahjong.packets import *
from mahjong.poll import *
from mahjong.shared import *

GamePlayerTuple = Tuple[
    'ServerGamePlayer',
    'ServerGamePlayer',
    'ServerGamePlayer',
    'ServerGamePlayer',
]


DrawPlayerTuple = Tuple[
    'GameDrawPlayer',
    'GameDrawPlayer',
    'GameDrawPlayer',
    'GameDrawPlayer',
]


class Server:
  def __init__(self, poll: Poll, address: Tuple[str, int]):
    self.poll = poll
    self.address = address
    self.socket: socket.socket
    self.state: ServerState = LobbyServerState(self)

  def start(self):
    host, port = self.address

    self.socket = socket.socket()
    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.socket.bind((host, port))

    print(f'Server is listing on the port {port}...')
    self.socket.listen()

    self.poll.register(self.socket, select.POLLIN, self.on_server_data)

  def on_server_data(self, fd: socket.socket, event: int):
    self.state.on_server_data(fd, event)

  def on_client_data(self, fd: socket.socket, event: int):
    self.state.on_client_data(fd, event)


class ServerState:
  def __init__(self, server: Server):
    self.server = server

  @property
  def state(self):
    return self.server.state

  @state.setter
  def state(self, state: 'ServerState'):
    self.server.state = state

  @property
  def poll(self):
    return self.server.poll

  def on_server_data(self, server: socket.socket, event: int):
    if event & select.POLLIN:
      client, address = server.accept()
      self.on_client_connect(client, address)
      self.poll.register(client, select.POLLIN, self.server.on_client_data)

  def on_client_data(self, client: socket.socket, event: int):
    if event & select.POLLHUP:
      self.on_client_disconnect(client)
    elif event & select.POLLIN:
      packet = read_packet(client)
      if packet is not None:
        self.on_client_packet(client, packet)

  def on_client_connect(self, client: socket.socket, address: Tuple[str, int]):
    pass

  def on_client_disconnect(self, client: socket.socket):
    self.poll.unregister(client)
    client.close()

  def on_client_packet(self, client: socket.socket, packet: Packet):
    pass


class ServerLobbyPlayer(ClientMixin):
  def __init__(self, client: socket.socket):
    self.client = client


class LobbyServerState(ServerState):
  def __init__(self, server: Server):
    self.server = server
    self.clients: Dict[socket.socket, ServerLobbyPlayer] = {}

  def on_client_connect(self, client: socket.socket, address: Tuple[str, int]):
    self.clients[client] = ServerLobbyPlayer(client)

    if len(self.clients) == len(Wind):
      player1, player2, player3, player4 = self.clients.values()
      self.state = GameServerState(self.server, (
          player1,
          player2,
          player3,
          player4,
      ))

  def on_client_disconnect(self, client: socket.socket):
    super().on_client_disconnect(client)
    del self.clients[client]


class ServerGamePlayer(ClientMixin, GamePlayerMixin):
  def __init__(self, client: socket.socket, points: int, riichi: bool = False):
    self.client = client
    self.points = points
    self.riichi = riichi

  def declare_riichi(self):
    if not self.riichi:
      self.riichi = True
      self.points -= RIICHI_POINTS

  def take_points(self, other: 'ServerGamePlayer', points: int):
    self.points += points
    other.points -= points


class GameServerState(ServerState, GameStateMixin):
  def __init__(
      self, server: Server, players: Tuple[ServerLobbyPlayer, ServerLobbyPlayer, ServerLobbyPlayer, ServerLobbyPlayer], starting_points: int = 25000,
      hand: int = 0, repeat: int = 0, bonus_honba=0, bonus_riichi=0, max_rounds: int = 1
  ):
    self.server = server

    self.hand = hand
    self.repeat = repeat
    self.bonus_honba = bonus_honba
    self.bonus_riichi = bonus_riichi
    self.max_rounds = max_rounds

    self.players: GamePlayerTuple = (
        ServerGamePlayer(players[0].client, starting_points),
        ServerGamePlayer(players[1].client, starting_points),
        ServerGamePlayer(players[2].client, starting_points),
        ServerGamePlayer(players[3].client, starting_points),
    )
    self.update_player_states()

  def on_client_packet(self, client: socket.socket, packet: Packet):
    player = self.player_for_client(client)
    if isinstance(packet, RiichiClientPacket):
      self.on_player_riichi(player, packet)
    elif isinstance(packet, TsumoClientPacket):
      self.on_player_tsumo(player, packet)
    elif isinstance(packet, RonClientPacket):
      self.on_player_ron(player, packet)
    elif isinstance(packet, DrawClientPacket):
      self.on_player_draw(player, packet)

  def on_player_riichi(self, player: ServerGamePlayer, packet: RiichiClientPacket):
    player.declare_riichi()
    self.update_player_states()

  def on_player_tsumo(self, player: ServerGamePlayer, packet: TsumoClientPacket):
    self.take_riichi_points([player])

    for other_player in self.players:
      if other_player == player:
        continue

      other_player_wind = self.player_wind(other_player)
      points: int
      if other_player_wind == Wind.EAST:
        points = packet.dealer_points
      else:
        points = packet.points
      points += self.total_honba * TSUMO_HONBA_POINTS
      player.take_points(other_player, points)

    if self.player_wind(player) == Wind.EAST:
      self.repeat_hand()
    else:
      self.next_hand()

  def on_player_ron(self, player: ServerGamePlayer, packet: RonClientPacket):
    if self.player_wind(player) == packet.from_wind:
      return

    ron_players = []
    for wind, other_player in self.players_by_wind:
      points: Optional[int]
      if wind == packet.from_wind:
        continue
      elif player == other_player:
        points = packet.points
      else:
        points = None
      ron_players.append(GameRonPlayer(other_player.client, points))

    self.state = GameRonServerState(
        self.server, self, packet.from_wind, ron_players)

  def on_player_ron_complete(self, from_wind: Wind, ron_players: List['GameRonPlayer']):
    winners = [
        (self.player_for_client(player.client), player.points)
        for player in ron_players
    ]
    self.take_riichi_points([
        player
        for (player, _) in winners
    ])

    discarder: ServerGamePlayer = self.player_for_wind(from_wind)
    for (player, points) in winners:
      points = points + (self.total_honba * RON_HONBA_POINTS)
      player.take_points(discarder, points)

    repeat = any((
        self.player_wind(player) == Wind.EAST
        for (player, _) in winners
    ))
    if repeat:
      self.repeat_hand()
    else:
      self.next_hand()

  def on_player_draw(self, player: ServerGamePlayer, packet: DrawClientPacket):
    player1, player2, player3, player4 = self.players
    players = (
        GameDrawPlayer(player1.client, packet.tenpai if player ==
                       player1 else None),
        GameDrawPlayer(player2.client, packet.tenpai if player ==
                       player2 else None),
        GameDrawPlayer(player3.client, packet.tenpai if player ==
                       player3 else None),
        GameDrawPlayer(player4.client, packet.tenpai if player ==
                       player4 else None),
    )
    self.state = GameDrawServerState(self.server, self, players)

  def on_player_draw_complete(self, players: DrawPlayerTuple):
    for draw_player in players:
      if not draw_player.tenpai:
        continue

      player = self.player_for_client(draw_player.client)
      for other_player in self.players:
        if player == other_player:
          continue

        player.take_points(other_player, DRAW_POINTS)

    east_index = self.player_index_for_wind(Wind.EAST)
    if players[east_index].tenpai:
      self.repeat_hand(draw=True)
    else:
      self.next_hand(draw=True)

  def take_riichi_points(self, winners: List[ServerGamePlayer]):
    winner = next((
        player
        for _, player in self.players_by_wind
        if player in winners
    ))

    winner.points += (RIICHI_POINTS * self.total_riichi)
    self.bonus_riichi = 0

  def reset_player_riichi(self):
    for player in self.players:
      player.riichi = False

  def repeat_hand(self, draw=False):
    if draw:
      self.bonus_riichi = self.total_riichi
    else:
      self.bonus_riichi = 0

    self.reset_player_riichi()
    self.repeat += 1
    self.update_player_states()

  def next_hand(self, draw=False):
    if draw:
      self.bonus_honba = self.repeat + 1
      self.bonus_riichi = self.total_riichi
    else:
      self.bonus_honba = 0
      self.bonus_riichi = 0

    self.reset_player_riichi()
    self.hand += 1
    self.repeat = 0
    self.update_player_states()

  def player_for_client(self, client: socket.socket):
    return next((
        player
        for player in self.players
        if player.client == client
    ))

  def update_player_states(self):
    for index, player in enumerate(self.players):
      packet = PlayerGameStateServerPacket(
          self.hand,
          self.repeat,
          self.bonus_honba,
          self.bonus_riichi,
          index,
          self.players,
      ).pack()
      send_msg(player.client, packet)


class GameDrawPlayer(ClientMixin):
  def __init__(self, client: socket.socket, tenpai: Optional[bool]):
    self.client = client
    self.tenpai = tenpai


class GameDrawServerState(ServerState):
  def __init__(self, server: Server, game_state: GameServerState, players: DrawPlayerTuple):
    self.server = server
    self.game_state = game_state

    self.players = players
    for player in players:
      if player.tenpai is not None:
        continue
      send_msg(player.client, DrawServerPacket().pack())

  def on_client_packet(self, client: socket.socket, packet: Packet):
    player = self.player_for_client(client)
    if isinstance(packet, DrawClientPacket):
      self.on_player_draw(player, packet)

  def on_player_draw(self, player: GameDrawPlayer, packet: DrawClientPacket):
    player.tenpai = packet.tenpai

    if all((player.tenpai is not None for player in self.players)):
      self.state = self.game_state
      self.state.on_player_draw_complete(self.players)

  def player_for_client(self, client: socket.socket):
    return next((
        player
        for player in self.players
        if player.client == client
    ))


class GameRonPlayer(ClientMixin):
  def __init__(self, client: socket.socket, points: Optional[int]):
    self.client = client
    self.points = points


class GameRonServerState(ServerState):
  def __init__(self, server: Server, game_state: GameServerState, from_wind: Wind, players: List[GameRonPlayer]):
    self.server = server
    self.game_state = game_state

    self.from_wind = from_wind
    self.players = players
    for player in players:
      if player.points is not None:
        continue
      send_msg(player.client, RonServerPacket(from_wind).pack())

  def on_client_packet(self, client: socket.socket, packet: Packet):
    player = self.player_for_client(client)
    if isinstance(packet, RonClientPacket):
      self.on_player_ron(player, packet)

  def on_player_ron(self, player: GameRonPlayer, packet: RonClientPacket):
    player.points = packet.points

    if all((player.points is not None for player in self.players)):
      self.state = self.game_state
      self.state.on_player_ron_complete(self.from_wind, [
          player
          for player in self.players
          if player.points is not None and player.points > 0
      ])

  def player_for_client(self, client: socket.socket):
    return next((
        player
        for player in self.players
        if player.client == client
    ))


def main():
  try:
    poll = Poll()
    server = Server(poll, ('127.0.0.1', 1246))
    server.start()
    client = Client(poll, ('127.0.0.1', 1246))
    client.start()
    while True:
      poll.poll()
  finally:
    poll.close()


if __name__ == '__main__':
  main()
