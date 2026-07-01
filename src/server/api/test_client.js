const socket = new WebSocket("ws://localhost:8000/ws/action");

socket.onopen = () => {
  socket.send(
    JSON.stringify({
      match_id: "default",
      player_id: 1,
      action_kind: "draw_card",
    }),
  );
};

socket.onmessage = (event) => {
  console.log("Response:", JSON.parse(event.data));
};
