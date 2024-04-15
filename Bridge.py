import subprocess
import wave
from datetime import datetime
from socket import *

from flask import Flask, request

app = Flask(__name__)

ai_socket = socket()
ai_host = "127.0.0.1"
ai_port = 2100
ai_socket.connect((ai_host, ai_port))  # connect to the server


@app.route('/', methods=['POST'])
def receive_post():
    global ai_socket
    channels = 1
    fs = 16000  # Record at 44100 samples per second
    now = datetime.now()
    format_string = "%Y-%m-%d_%H:%M:%S"
    datetime_str = now.strftime(format_string)
    filename = datetime_str + ".wav"

    # Access the POST data from the request object
    data = request.data
    # Save the recorded data as a WAV file
    wave_file = wave.open(filename, 'wb')
    wave_file.setnchannels(channels)
    wave_file.setsampwidth(2)
    wave_file.setframerate(fs)
    wave_file.writeframes(data)
    wave_file.close()

    command = "./whisper -m models/ggml-tiny.en.bin -f " + filename + " -t 15 -nt -np"
    transcription = subprocess.check_output(command, shell=True)
    ai_socket.sendall(transcription + b"\n\n")
    return_code = ai_socket.recv(1024)
    if return_code:
        return "", 200
    else:
        return "", 503


if __name__ == '__main__':
    app.run(debug=False)
