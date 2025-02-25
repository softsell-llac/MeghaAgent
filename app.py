import io
import re
import sys
import base64
import json
import signal
import logging
import threading
import time
import os

from google.cloud import speech_v1p1beta1 as speech
from flask import Flask, request
from flask_sockets import Sockets
from six.moves import queue
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler

app = Flask(__name__)
sockets = Sockets(app)

@app.route('/media', methods=['GET'])
def media_http():
    app.logger.info("HTTP request received for /media")
    return "Media endpoint is live", 200

class Stream(object):
    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk
        self.buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self.closed = False
        return self

    def __exit__(self, type, value, traceback):
        self.closed = True
        self.buff.put(None)

    def fill_buffer(self, in_data):
        self.buff.put(in_data)

    def generator(self):
        while True:
            chunk = self.buff.get()
            if chunk is None:
                return
            data = [chunk]

            while True:
                try:
                    chunk = self.buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b"".join(data)

@sockets.route('/media')
def media(ws):
    if not ws:
        app.logger.error("WebSocket connection failed: handshake not established")
        return
    app.logger.info("WebSocket connection established")
    stream = Stream(RATE, CHUNK)

    threading.Thread(target=stream_transcript, args=(ws, stream)).start()

    while not ws.closed:
        message = ws.receive()
        if message is None:
            continue

        data = json.loads(message)

        if data['event'] == 'media':
            chunk = base64.b64decode(data['media']['payload'])
            stream.fill_buffer(chunk)

        if data['event'] == 'stop':
            app.logger.info("WebSocket connection closing")
            break



def stream_transcript(ws, stream):
    audio_generator = stream.generator()
    requests = (speech.StreamingRecognizeRequest(audio_content=content) for content in audio_generator)
    responses = client.streaming_recognize(streaming_config, requests)

    for response in responses:
        for result in response.results:
            if result.is_final:
                transcript = result.alternatives[0].transcript
                app.logger.info(f"Transcription: {transcript}")

                response_message = get_scripted_response(transcript)
                ws.send(json.dumps({
                    'event': 'bot_response',
                    'message': response_message
                }))


def get_scripted_response(transcription):
    transcription = transcription.lower()
    if "hello" in transcription:
        return "Hello! How can I help you today?"
    if "price" in transcription:
        return "Our prices vary depending on the product. Can you specify what you’re looking for?"
    if "support" in transcription:
        return "I’ll connect you with our support team. Please hold on."
    return "I’m sorry, I didn’t catch that. Could you repeat?"

if __name__ == '__main__':
    RATE = 8000
    CHUNK = int(RATE / 10)

    client = speech.SpeechClient()

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code="en-IN"
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config, interim_results=True
    )

    port = int(os.environ.get('PORT', 5000))  # Default to 5000 if PORT isn't set
server = pywsgi.WSGIServer(('', port), app, handler_class=WebSocketHandler)
app.logger.info("Server started on ws://localhost:5000/media")
server.serve_forever()