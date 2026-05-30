const socket = new WebSocket("ws://localhost:8000/ws");

socket.onopen = () => {
  socket.send(JSON.stringify({ player_id: 42 }));
};

socket.onmessage = (event) => {
  console.log("Response:", JSON.parse(event.data));
};
