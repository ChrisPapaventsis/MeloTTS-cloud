import functions_framework
import os
import warnings
import tempfile
import re # For parsing GCS URI
from cloudevents.http import CloudEvent # For type hinting

# Import GCS client library
from google.cloud import storage

from api import TTS

# --- GCS Client Initialization ---
try:
    storage_client = storage.Client()
except Exception as e:
    print(f"Failed to initialize Google Cloud Storage client: {e}")
    storage_client = None # Function will fail if GCS is needed

# --- Configuration from Environment Variables ---
# Define these in your Cloud Run service configuration
OUTPUT_GCS_BUCKET_NAME = os.environ.get('OUTPUT_GCS_BUCKET_NAME')
DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'EN').upper()
DEFAULT_SPEAKER = os.environ.get('DEFAULT_SPEAKER', 'EN-Default')
DEFAULT_SPEED = float(os.environ.get('DEFAULT_SPEED', 1.0))
DEFAULT_DEVICE = os.environ.get('DEFAULT_DEVICE', 'cpu')

# --- GCS Text Reading Helper (largely unchanged) ---
def parse_gcs_uri(gcs_uri: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"gs://([^/]+)/(.+)", gcs_uri)
    if match:
        return match.group(1), match.group(2)
    return None

def read_text_from_gcs(bucket_name: str, blob_name: str) -> str:
    if not storage_client:
        raise RuntimeError("Google Cloud Storage client is not available.")
    
    gcs_uri = f"gs://{bucket_name}/{blob_name}" # For error messages
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        if not blob.exists():
            raise FileNotFoundError(f"File not found in GCS: {gcs_uri}")
            
        file_content_bytes = blob.download_as_bytes()
        return file_content_bytes.decode('utf-8').strip()
    except storage.exceptions.NotFound:
        raise FileNotFoundError(f"File not found in GCS: {gcs_uri}")
    except storage.exceptions.Forbidden as e:
        raise PermissionError(f"Permission denied accessing GCS file: {gcs_uri}. Ensure the service account has 'Storage Object Viewer'. Original error: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to read file from GCS '{gcs_uri}': {type(e).__name__} - {e}")

# --- GCS File Upload Helper ---
def upload_to_gcs(local_file_path: str, bucket_name: str, destination_blob_name: str):
    if not storage_client:
        raise RuntimeError("Google Cloud Storage client is not available for upload.")
    if not bucket_name:
        raise ValueError("OUTPUT_GCS_BUCKET_NAME environment variable is not set.")

    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_file_path)
        print(f"Successfully uploaded '{local_file_path}' to 'gs://{bucket_name}/{destination_blob_name}'")
    except storage.exceptions.Forbidden as e:
        raise PermissionError(f"Permission denied uploading to GCS bucket '{bucket_name}'. Ensure the service account has 'Storage Object Creator' role. Original error: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to upload file to GCS 'gs://{bucket_name}/{destination_blob_name}': {type(e).__name__} - {e}")


@functions_framework.cloud_event
def melo_tts_gcs_trigger(cloud_event: CloudEvent):
    """
    Cloud Event Function for Text-to-Speech triggered by a GCS file upload.
    Processes the uploaded text file and saves the audio to another GCS bucket.
    """
    if not storage_client:
        print("Error: GCS client not initialized. Cannot proceed.")
        # This will cause the function to fail and potentially retry if configured.
        raise RuntimeError("GCS client failed to initialize.")

    if not OUTPUT_GCS_BUCKET_NAME:
        print("Error: OUTPUT_GCS_BUCKET_NAME environment variable is not set. Cannot determine output location.")
        raise ValueError("Missing OUTPUT_GCS_BUCKET_NAME configuration.")

    # --- 1. Extract file information from the CloudEvent ---
    event_data = cloud_event.data
    input_bucket_name = event_data.get("bucket")
    input_blob_name = event_data.get("name")

    if not input_bucket_name or not input_blob_name:
        print(f"Error: Malformed CloudEvent data. Missing 'bucket' or 'name'. Data: {event_data}")
        # Ack the event to prevent retries for a malformed event
        return ('Malformed event data', 200) # Or simply return to ack

    print(f"Received event for file: gs://{input_bucket_name}/{input_blob_name}")

    # Optional: Filter for specific file types, e.g., .txt
    if not input_blob_name.lower().endswith(".txt"):
        print(f"File {input_blob_name} is not a .txt file. Skipping processing.")
        return ('Not a TXT file, skipping.', 200) # Ack the event

    # --- 2. Read text content from the trigger GCS file ---
    text_content = None
    try:
        text_content = read_text_from_gcs(input_bucket_name, input_blob_name)
    except (ValueError, FileNotFoundError, PermissionError, RuntimeError) as e:
        # These errors are critical for this specific file, log and terminate processing for this event.
        # The event will be acked, so it won't retry indefinitely for a bad file/permissions.
        print(f"Error reading source GCS file gs://{input_bucket_name}/{input_blob_name}: {e}")
        # Depending on the error, you might want to raise it to signal failure for retry
        # For FileNotFoundError or PermissionError, retrying might not help.
        # For temporary RuntimeError, a retry might be configured via Cloud Functions/Run.
        # For this example, we'll just log and exit successfully for the event.
        return (f"Error processing input file: {e}", 500 if isinstance(e, RuntimeError) else 200)


    if not text_content:
        print(f"Error: The file gs://{input_bucket_name}/{input_blob_name} is empty or could not be read.")
        return (f'Empty or unreadable input file.', 200) # Ack the event

    # --- 3. TTS Parameters (from environment variables or defaults) ---
    language = DEFAULT_LANGUAGE # Already uppercased
    speaker_input = DEFAULT_SPEAKER
    speed = DEFAULT_SPEED
    device = DEFAULT_DEVICE

    valid_languages = ['EN', 'ES', 'FR']
    if language not in valid_languages:
        print(f"Error: Invalid language from environment: '{language}'. Must be one of {valid_languages}")
        # This is a configuration error, fail the function.
        raise ValueError(f"Invalid configured language: {language}")

    temp_output_path = None
    try:
        # --- 4. Initialize TTS model ---
        print(f"Initializing TTS model for language: {language}, device: {device}")
        model = TTS(language=language, device=device)
        speaker_ids = model.hps.data.spk2id
        print(f"Available speaker IDs for {language}: {speaker_ids}")


        selected_speaker_id_for_tts = None

        # --- 5. Determine speaker ID ---
        if language == 'EN':
            speaker_name_to_use = speaker_input # e.g. 'EN-Default' or 'EN-US' from env
            valid_en_speakers = ['EN-Default', 'EN-US', 'EN-BR', 'EN_INDIA', 'EN-AU'] # As per your original code
            if speaker_name_to_use not in valid_en_speakers:
                print(f"Warning: Invalid speaker '{speaker_name_to_use}' for English from environment. Using 'EN-Default'. Must be one of {valid_en_speakers}.")
                speaker_name_to_use = 'EN-Default' # Fallback
            
            if speaker_name_to_use not in speaker_ids:
                print(f"Error: Speaker '{speaker_name_to_use}' not found in model for English. Available: {list(speaker_ids.keys())}. Using first available.")
                if not speaker_ids: raise KeyError(f"No speakers available in model for EN")
                selected_speaker_id_for_tts = list(speaker_ids.values())[0] # Fallback to first available
            else:
                selected_speaker_id_for_tts = speaker_ids[speaker_name_to_use]
        else: # Non-English
            # Your original code implies non-EN languages might ignore speaker_input or use a default.
            # We use the default speaker for the language if speaker_input isn't relevant or found.
            if not speaker_ids:
                raise KeyError(f"Error: No speakers available for language '{language}' in the model.")
            
            # Try to use the provided speaker_input if it exists for this language
            if speaker_input in speaker_ids:
                 selected_speaker_id_for_tts = speaker_ids[speaker_input]
                 print(f"Info: For language '{language}', using configured speaker '{speaker_input}'.")
            else:
                first_available_speaker_key = list(speaker_ids.keys())[0]
                selected_speaker_id_for_tts = speaker_ids[first_available_speaker_key]
                actual_warning_msg = f"Warning: Speaker setting ('{speaker_input}') not found or applicable for non-English language '{language}'. Using default speaker '{first_available_speaker_key}'."
                warnings.warn(actual_warning_msg)
                print(actual_warning_msg)

        print(f"Using text: \"{text_content[:100]}...\"") # Log beginning of text
        print(f"TTS settings: Lang={language}, SpeakerID={selected_speaker_id_for_tts}, Speed={speed}")

        # --- 6. Perform TTS ---
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmpfile:
            temp_output_path = tmpfile.name
        
        model.tts_to_file(text_content, selected_speaker_id_for_tts, temp_output_path, speed=speed)
        print(f"TTS audio generated successfully at: {temp_output_path}")

        # --- 7. Upload the audio file to the output GCS bucket ---
        # Construct a meaningful output file name, e.g., original_name.wav
        base_name = os.path.splitext(input_blob_name)[0]
        output_blob_name = f"{base_name}.wav" # You might want to put it in a subdirectory e.g. "tts_outputs/"

        upload_to_gcs(temp_output_path, OUTPUT_GCS_BUCKET_NAME, output_blob_name)
        
        # If event-driven, a successful completion is an implicit "OK".
        # No explicit HTTP response like flask.send_file is returned.
        print(f"Processing complete for gs://{input_bucket_name}/{input_blob_name}. Output: gs://{OUTPUT_GCS_BUCKET_NAME}/{output_blob_name}")
        return ('Processing successful', 200) # Explicitly ack the event (optional for GCS triggers if no error)

    except (ValueError, KeyError) as e: # Configuration or data errors
        print(f"Error during TTS processing (ValueError/KeyError): {e}")
        # This indicates a problem with the setup or the data that might not be solvable by retry
        # Ack the event by returning normally (or a specific error code not triggering retry)
        raise # Fail the function invocation for bad config/data
    except Exception as e:
        import traceback
        print(f"An unexpected error occurred during TTS processing: {e}\n{traceback.format_exc()}")
        # This will cause the function invocation to be marked as failed,
        # and it might be retried depending on Cloud Run/Eventarc settings.
        raise
    finally:
        # --- 8. Clean up the temporary file ---
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
                print(f"Temporary file {temp_output_path} removed.")
            except Exception as e_remove:
                print(f"Error removing temporary file {temp_output_path}: {e_remove}")