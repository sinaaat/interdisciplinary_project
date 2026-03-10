import ee
import requests
from PIL import Image
from io import BytesIO
import numpy as np
import torch
from datetime import datetime, timedelta
import os
from segment_anything import sam_model_registry, SamPredictor
from PIL import ImageEnhance

ee.Authenticate() # Authenticate to the Earth Engine servers

ee.Initialize(project='ee-ahmadisina1993') # Initialize the Earth Engine library

# Define the bounding box for the region of interest
min_lon = 16.594524  # Minimum longitude
min_lat = 48.184153  # Minimum latitude
max_lon = 16.628869  # Maximum longitude
max_lat = 48.197361  # Maximum latitude

# Create a rectangle geometry for the region of interest
geometry = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])

# define the time range and time step in months
start_date_str = '2019-04-01'
end_date_str = '2019-09-01'
time_step_days = 30 # in months

# Convert string dates to ee.Date objects
start_ee_date = ee.Date(start_date_str)
end_ee_date = ee.Date(end_date_str)

# Calculate the total number of days between start and end dates
total_days = end_ee_date.difference(start_ee_date, 'day').getInfo()
num_steps = total_days // time_step_days + 1

# Create a list of step indices
step_indices = ee.List.sequence(0, num_steps - 1)

# Load the SamSeg model
sam_checkpoint = "sam_vit_h_4b8939.pth"  # Downloaded from Meta's repository
model_type = "vit_h"  # The type of Sam model you are using (e.g., 'vit_h', 'vit_l')
sam = sam_model_registry[model_type](checkpoint=sam_checkpoint)
sam_predictor = SamPredictor(sam)

# Function to advance the start date by step * time_step_days
def advance_date(step):
    # This converts the step index to an ee.Number, which allows it to be multiplied by an integer within Earth Engine.
    step_number = ee.Number(step)
    # Earth Engine's method to perform multiplication.
    return start_ee_date.advance(step_number.multiply(time_step_days), 'day')

# Generate the list of dates
date_list = step_indices.map(advance_date)


# Function to save the RGB image
def save_rgb_image(image, date_str, output_dir='rgb_images'):
    os.makedirs(output_dir, exist_ok=True)

    # Select RGB bands (B4: red, B3: green, B2: blue)
    image_rgb = image.select(['B4', 'B3', 'B2']).visualize(min=0, max=3000)

    # Get the download URL
    url = image_rgb.getDownloadURL({
        'scale': 5,  # Adjust scale as needed
        'region': geometry,
        'format': 'PNG'
    })

    # Download and save the RGB image
    response = requests.get(url)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content)).convert("RGB")
        # Increase sharpness and contrast
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(2.0)  # Increase sharpness by 2x
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)  # Increase contrast by 1.5x

        file_path = os.path.join(output_dir, f'rgb_{date_str}.png')
        img.save(file_path)
        print(f"Saved high-quality RGB image to {file_path}")
        return img  # Return the image to be passed to SamSeg
    else:
        print(f"Failed to download RGB image for {date_str}")
        return None


# Function to save the segmented image using SamSeg
def save_segmented_image(img, date_str, sam_predictor, output_dir='segmented_images'):
    os.makedirs(output_dir, exist_ok=True)

    # Convert the RGB image to a numpy array and set it for SamSeg
    img_array = np.array(img)
    sam_predictor.set_image(img_array)

    # Perform segmentation
    masks, _, _ = sam_predictor.predict(
        point_coords=None, point_labels=None, multimask_output=False
    )

    # Save the segmented image
    segmented_img = Image.fromarray(masks[0].astype(np.uint8) * 255)  # Convert mask to binary image
    file_path = os.path.join(output_dir, f'segmented_{date_str}.png')
    segmented_img.save(file_path)
    print(f"Saved segmented image to {file_path}")

# Function to fetch images from GEE
def fetch_images(geometry, date):
    collection = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                  .filterDate(date, ee.Date(date).advance(time_step_days, 'day'))
                  .filterBounds(geometry)
                  # .map(cloud_mask_s2)
                  .sort('system:time_start'))

    image = collection.first()

    # Check if the image is not None
    if image is None:
        print(f"No image found for {date}")
        return None

    # Check if the image contains the required bands
    try:
        band_names = image.bandNames().getInfo()
        required_bands = ['B4', 'B3', 'B2']
        print(required_bands)
        if all(band in band_names for band in required_bands):
            return image
        else:
            print(f"Image for {date} does not contain the required bands: {band_names}")
            return None
    except Exception as e:
        print(f"Error retrieving band names for {date}: {e}")
        return None


# Iterate through each date and process images
for date in date_list.getInfo():
    # The date is returned as a dictionary with a 'value' field (timestamp in milliseconds)
    date_timestamp = date['value']

    # Convert the timestamp back to a readable date string in Python
    date_str = datetime.utcfromtimestamp(date_timestamp / 1000).strftime('%Y-%m-%d')
    print(f"Processing date: {date_str}")

    # Fetch image for the current date
    image = fetch_images(geometry, date_str)

    if image:
        # Save the RGB image and get the image in a format that can be used for segmentation
        img = save_rgb_image(image, date_str)

        if img:
            # Save the segmented image using SamSeg
            save_segmented_image(img, date_str, sam_predictor)
    else:
        print(f"No image found or an error occurred for {date_str}, skipping.")

