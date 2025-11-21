import os
import subprocess

def run_server():
    print("Starting RTSP Server on port 5544...")
    subprocess.Popen(["python", "Server.py", "5544"])
    print("Server started.\n")

def run_client():
    print("Starting RTP Client...")
    subprocess.Popen(["python", "ClientLauncher.py", "localhost", "5544", "5000", "movie.Mjpeg"])
    print("Client started.\n")

def main():
    while True:
        print("\n===== MENU =====")
        print("1. Run Server")
        print("2. Run Client")
        print("3. Exit")
        choice = input("Choose an option: ")

        if choice == "1":
            run_server()
        elif choice == "2":
            run_client()
        elif choice == "3":
            print("Exiting...")
            break
        else:
            print("Invalid choice, try again!")

if __name__ == "__main__":
    main()
