"""
Zenno Desktop Agent - Entry Point
"""
import time

def main():
    print("Zenno Agent Started")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nAgent stopped")

if __name__ == "__main__":
    main()
