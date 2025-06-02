

if __name__ == '__main__':

    from api import TTS
    device = 'auto'
    models = {
        'EN': TTS(language='EN', device=device),
        'ES': TTS(language='ES', device=device),
        'FR': TTS(language='FR', device=device),
    }