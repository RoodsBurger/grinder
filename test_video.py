#!/usr/bin/python3
"""
Simple video playback test - isolated from motor control
Based on tobias/robot/how_to_walk/display/display_video.py
"""
import time
import cv2
from PIL import Image
from lcd_display import LCD_1inch28

def preload_video_frames(video_path, target_size=(240, 240)):
    """Convert video to properly sized frames"""
    frames = []
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"Error: Could not open video file {video_path}")
        return frames, 30
        
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    print(f"Loading video: {video_path}")
    print(f"FPS: {fps}")
    
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Convert to PIL Image and resize
        pil_image = Image.fromarray(frame_rgb)
        pil_image = pil_image.resize(target_size, Image.Resampling.LANCZOS)
        
        frames.append(pil_image)
        frame_count += 1
    
    cap.release()
    print(f"Loaded {frame_count} frames")
    return frames, fps

def main():
    try:
        # Initialize display
        print("Initializing display...")
        disp = LCD_1inch28()
        disp.init_display()
        disp.clear()
        print("Display initialized")
        
        # Load video frames
        video_path = "grinder_video.mp4"
        frames, fps = preload_video_frames(video_path)
        
        if not frames:
            print("No frames loaded. Exiting.")
            return
            
        frame_delay = 1.0 / fps
        print(f"\nPlaying video at {fps} FPS (delay: {frame_delay:.4f}s per frame)")
        print("Press Ctrl+C to stop\n")
        
        # Main playback loop
        frame_times = []
        try:
            while True:  # Loop forever
                for i, frame in enumerate(frames):
                    start_time = time.time()
                    
                    # Display the frame
                    disp.show_image(frame)
                    
                    # Calculate actual timing
                    elapsed_time = time.time() - start_time
                    frame_times.append(elapsed_time)
                    
                    # Print stats every 30 frames
                    if (i + 1) % 30 == 0:
                        avg_time = sum(frame_times[-30:]) / 30
                        actual_fps = 1.0 / avg_time if avg_time > 0 else 0
                        print(f"Frame {i+1}/{len(frames)}: {elapsed_time*1000:.1f}ms ({actual_fps:.1f} FPS)")
                    
                    # Precise timing
                    if elapsed_time < frame_delay:
                        time.sleep(frame_delay - elapsed_time)
                        
        except KeyboardInterrupt:
            print("\nPlayback stopped by user")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        try:
            disp.clear()
            disp.module_exit()
        except:
            pass
        print("Display cleaned up")

if __name__ == "__main__":
    main()
