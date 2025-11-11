import os
import google_auth_httplib2
import google_auth_oauthlib
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = 'token.json'

def authenticate_youtube():
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)

    client_secrets_file = "client.json"

    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        client_secrets_file, SCOPES)
    credentials = flow.run_local_server()

    youtube = googleapiclient.discovery.build(
        "youtube", "v3", credentials=credentials)

    return youtube


def upload_video(youtube):
    request_body = {
        "snippet": {
            "categoryId": "22",
            "title": "Uploaded from Python",
            "description": "This is the most awesome description ever",
            "tags": ["test", "python", "api"]
        },
        "status": {
            "privacyStatus": "private"
        }
    }

    media_file = "outputs/among_us_compilation_2025-11-10_23-19-51.mp4"

    media = googleapiclient.http.MediaFileUpload(
        media_file,
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload {int(status.progress() * 100)}%")

        if response is not None:
            print(f"Video uploaded with ID: {response['id']}")


if __name__ == "__main__":
    youtube = authenticate_youtube()
    upload_video(youtube)


#giving up for now
#but we will be back