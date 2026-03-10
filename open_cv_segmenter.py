# Import necessary libraries
import numpy as np
import matplotlib.pyplot as plt
import cv2
from PIL import Image
from scipy.ndimage import label
import random

# Load the farmland image
image_path = "rgb_images/rgb_2019-04-01.png"
img = Image.open(image_path).convert("RGB")

# Convert to a NumPy array
img_array = np.array(img)

# Convert image to grayscale
gray_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

# Apply Otsu's thresholding for segmentation
_, binary_mask = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

# Label connected components (each separate field gets a unique label)
labeled_array, num_features = label(binary_mask)

# Generate random colors for each unique land segment
output_colored = np.zeros((*binary_mask.shape, 3), dtype=np.uint8)

# Assign a unique color to each segment
for i in range(1, num_features + 1):
    mask = labeled_array == i
    color = [random.randint(50, 255) for _ in range(3)]  # Generate random color
    output_colored[mask] = color  # Apply color to region

# Display the segmented farmland with unique colors
plt.figure(figsize=(8, 6))
plt.imshow(output_colored)
plt.axis("off")
plt.title("Segmented Farmland with Unique Colors")
plt.show()