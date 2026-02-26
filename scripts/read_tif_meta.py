from PIL import Image
from PIL.TiffTags import TAGS


def extract_tiff_metadata(image_path):
    try:
        with Image.open(image_path) as img:
            # Crucial for TIFFs: load the image data to ensure all tags are read
            img.load()

            # Accessing the tags via the 'tag_v2' attribute (a dictionary)
            # Iterate through the keys and use the TAGS dictionary to get human-readable names
            metadata = {}
            for tag_id, value in img.tag_v2.items():
                tag_name = TAGS.get(tag_id, tag_id)
                metadata[tag_name] = value

            print(f"--- Metadata for {image_path} ---")
            if not metadata:
                print("No metadata found or could not be read.")
                return

            for key, value in metadata.items():
                # Handling large strings or tuples for cleaner output if needed
                if isinstance(value, tuple) and len(value) > 100:
                   print(f"{key}: [Data tuple, length: {len(value)}]")
                else:
                    print(f"{key}: {value}")

    except IOError as e:
        print(f"Error opening image: {e}")


# Example usage:
extract_tiff_metadata('debug/pipeline_start/0002_ihs27_95.9.tif')
