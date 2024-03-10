import subprocess
import wave
from datetime import datetime
from socket import *

from flask import Flask, request

app = Flask(__name__)


@app.route('/', methods=['POST'])
def receive_post():
    channels = 1
    fs = 16000  # Record at 44100 samples per second
    now = datetime.now()
    format_string = "%Y-%m-%d_%H:%M:%S"
    datetime_str = now.strftime(format_string)
    filename = datetime_str + ".wav"

    # Access the POST data from the request object
    data = request.data
    # Save the recorded data as a WAV file
    waveFile = wave.open(filename, 'wb')
    waveFile.setnchannels(channels)
    waveFile.setsampwidth(2)
    waveFile.setframerate(fs)
    waveFile.writeframes(data)
    waveFile.close()

    print(subprocess.check_output("pwd"))
    command = "./whisper -m models/ggml-small.en.bin -f " + filename + " -t 15 -nt -np"
    transcription = subprocess.check_output(command, shell=True)

    host = gethostname()
    port = 4567
    with socket() as client_socket:
        client_socket.connect((host, port))  # connect to the server
        client_socket.sendall(transcription + b"\n\n")

    return ""


if __name__ == '__main__':
    app.run(debug=False)
