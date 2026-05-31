"""
ONVIF Utilities for RetailVision
Provides device discovery, management, and streaming capabilities for ONVIF-compliant devices.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

try:
    from onvif import ONVIFCamera
    from onvif.exceptions import ONVIFError
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    logging.warning("ONVIF libraries not available. Install onvif-zeep and zeep for ONVIF support.")

logger = logging.getLogger(__name__)


@dataclass
class ONVIFDeviceInfo:
    """Data class to store ONVIF device information."""
    name: str
    manufacturer: str
    model: str
    firmware_version: str
    serial_number: str
    hardware_id: str
    mac_address: Optional[str] = None
    ip_address: Optional[str] = None
    port: int = 80
    username: str = ""
    password: str = ""
    capabilities: Dict[str, Any] = None


@dataclass
class ONVIFStreamUri:
    """Data class to store ONVIF stream URI information."""
    stream_uri: str
    protocol: str  # RTSP, HTTP, etc.
    encoding: str  # JPEG, MPEG4, H264, etc.
    timeout: int


class ONVIFManager:
    """Manages ONVIF device discovery and communication."""
    
    def __init__(self):
        self.devices: Dict[str, ONVIFDeviceInfo] = {}
        self.cameras: Dict[str, ONVIFCamera] = {}
        self.discovery_thread = None
        self.discovery_active = False
        
    def discover_devices(self, timeout: int = 10) -> List[ONVIFDeviceInfo]:
        """
        Discover ONVIF devices on the network using WS-Discovery.
        
        Args:
            timeout: Discovery timeout in seconds
            
        Returns:
            List of discovered ONVIF devices
        """
        if not ONVIF_AVAILABLE:
            logger.error("ONVIF libraries not available for discovery")
            return []
            
        try:
            # Note: The onvif-zeep library doesn't include WS-Discovery by default
            # For production use, consider implementing WS-Discovery or using 
            # a library like pywsdiscovery
            logger.info("ONVIF device discovery would be implemented here")
            logger.info("For now, devices must be configured manually")
            return []
        except Exception as e:
            logger.error(f"Error during ONVIF device discovery: {e}")
            return []
    
    def connect_device(self, 
                      hostname: str, 
                      port: int = 80, 
                      username: str = "", 
                      password: str = "",
                      wsdl_dir: str = None) -> Optional[ONVIFCamera]:
        """
        Connect to an ONVIF device.
        
        Args:
            hostname: Device IP address or hostname
            port: Device port (default 80)
            username: Username for authentication
            password: Password for authentication
            wsdl_dir: Directory containing WSDL files (optional)
            
        Returns:
            ONVIFCamera object if connection successful, None otherwise
        """
        if not ONVIF_AVAILABLE:
            logger.error("ONVIF libraries not available")
            return None
            
        try:
            # Create ONVIF camera object
            camera = ONVIFCamera(hostname, port, username, password, wsdl_dir)
            
            # Test connection by getting device info
            device_info = camera.devicemanagement.GetDeviceInformation()
            
            # Store device info
            device_info_obj = ONVIFDeviceInfo(
                name=f"{hostname}:{port}",
                manufacturer=device_info.Manufacturer,
                model=device_info.Model,
                firmware_version=device_info.FirmwareVersion,
                serial_number=device_info.SerialNumber,
                hardware_id=device_info.HardwareId,
                ip_address=hostname,
                port=port,
                username=username,
                capabilities=self._get_capabilities(camera)
            )
            
            device_key = f"{hostname}:{port}"
            self.devices[device_key] = device_info_obj
            self.cameras[device_key] = camera
            
            logger.info(f"Connected to ONVIF device: {device_info_obj.name}")
            return camera
            
        except Exception as e:
            logger.error(f"Failed to connect to ONVIF device {hostname}:{port}: {e}")
            return None
    
    def _get_capabilities(self, camera: ONVIFCamera) -> Dict[str, Any]:
        """Get device capabilities."""
        try:
            capabilities = camera.devicemanagement.GetCapabilities()
            return {
                'device': str(capabilities.Device) if hasattr(capabilities, 'Device') else None,
                'events': str(capabilities.Events) if hasattr(capabilities, 'Events') else None,
                'imaging': str(capabilities.Imaging) if hasattr(capabilities, 'Imaging') else None,
                'media': str(capabilities.Media) if hasattr(capabilities, 'Media') else None,
                'ptz': str(capabilities.PTZ) if hasattr(capabilities, 'PTZ') else None,
                'analytics': str(capabilities.Analytics) if hasattr(capabilities, 'Analytics') else None,
                'analytics_module': str(capabilities.AnalyticsModule) if hasattr(capabilities, 'AnalyticsModule') else None,
                'device_io': str(capabilities.DeviceIO) if hasattr(capabilities, 'DeviceIO') else None,
                'display': str(capabilities.Display) if hasattr(capabilities, 'Display') else None,
                'recording': str(capabilities.Recording) if hasattr(capabilities, 'Recording') else None,
                'replay': str(capabilities.Replay) if hasattr(capabilities, 'Replay') else None,
                'search': str(capabilities.Search) if hasattr(capabilities, 'Search') else None,
                'receiver': str(capabilities.Receiver) if hasattr(capabilities, 'Receiver') else None,
            }
        except Exception as e:
            logger.warning(f"Could not get capabilities: {e}")
            return {}
    
    def get_stream_uri(self, 
                      camera_key: str, 
                      stream_setup: Dict[str, Any] = None) -> Optional[ONVIFStreamUri]:
        """
        Get stream URI from an ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            stream_setup: Stream setup configuration
            
        Returns:
            ONVIFStreamUri object if successful, None otherwise
        """
        if not ONVIF_AVAILABLE or camera_key not in self.cameras:
            logger.error("ONVIF not available or camera not connected")
            return None
            
        try:
            camera = self.cameras[camera_key]
            media_service = camera.create_media_service()
            
            # Get profiles
            profiles = media_service.GetProfiles()
            if not profiles:
                logger.error("No media profiles found on device")
                return None
                
            # Use first profile
            profile = profiles[0]
            
            # Default stream setup if not provided
            if stream_setup is None:
                stream_setup = {
                    'StreamSetup': {
                        'Stream': 'RTP-Unicast',
                        'Transport': {
                            'Protocol': 'RTSP'
                        }
                    }
                }
            
            # Get stream URI
            stream_uri = media_service.GetStreamUri({
                'StreamSetup': stream_setup['StreamSetup'],
                'ProfileToken': profile.token
            })
            
            return ONVIFStreamUri(
                stream_uri=stream_uri.Uri,
                protocol=stream_setup['StreamSetup']['Transport']['Protocol'],
                encoding=self._get_encoding_from_profile(profile),
                timeout=5  # Default timeout
            )
            
        except Exception as e:
            logger.error(f"Error getting stream URI for {camera_key}: {e}")
            return None
    
    def _get_encoding_from_profile(self, profile) -> str:
        """Extract encoding from media profile."""
        try:
            if hasattr(profile, 'VideoEncoderConfiguration'):
                if hasattr(profile.VideoEncoderConfiguration, 'Encoding'):
                    return str(profile.VideoEncoderConfiguration.Encoding)
            return "Unknown"
        except:
            return "Unknown"
    
    def get_snapshot_uri(self, camera_key: str) -> Optional[str]:
        """
        Get snapshot URI from an ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            
        Returns:
            Snapshot URI string if successful, None otherwise
        """
        if not ONVIF_AVAILABLE or camera_key not in self.cameras:
            logger.error("ONVIF not available or camera not connected")
            return None
            
        try:
            camera = self.cameras[camera_key]
            media_service = camera.create_media_service()
            
            # Get profiles
            profiles = media_service.GetProfiles()
            if not profiles:
                logger.error("No media profiles found on device")
                return None
                
            # Use first profile
            profile = profiles[0]
            
            # Get snapshot URI
            snapshot_uri = media_service.GetSnapshotUri({
                'ProfileToken': profile.token
            })
            
            return snapshot_uri.Uri
            
        except Exception as e:
            logger.error(f"Error getting snapshot URI for {camera_key}: {e}")
            return None
    
    def ptz_move(self, 
                camera_key: str, 
                pan_tilt: Dict[str, float] = None, 
                zoom: Dict[str, float] = None,
                speed: Dict[str, float] = None) -> bool:
        """
        Send PTZ movement command to an ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            pan_tilt: Pan/tilt vector {x: float, y: float} where -1 to 1
            zoom: Zoom vector {x: float} where -1 to 1
            speed: Speed vector {pan_tilt: float, zoom: float} where 0 to 1
            
        Returns:
            True if command sent successfully, False otherwise
        """
        if not ONVIF_AVAILABLE or camera_key not in self.cameras:
            logger.error("ONVIF not available or camera not connected")
            return False
            
        try:
            camera = self.cameras[camera_key]
            ptz_service = camera.create_ptz_service()
            
            # Get PTZ configuration options to get valid ranges
            request = ptz_service.create_type('GetConfigurationOptions')
            request.ConfigurationToken = ptz_service.GetStatus().PTZConfiguration.token if ptz_service.GetStatus() else None
            
            if not request.ConfigurationToken:
                # Try to get configuration from profiles
                media_service = camera.create_media_service()
                profiles = media_service.GetProfiles()
                for profile in profiles:
                    if hasattr(profile, 'PTZConfiguration') and profile.PTZConfiguration:
                        request.ConfigurationToken = profile.PTZConfiguration.token
                        break
            
            if not request.ConfigurationToken:
                logger.warning("Could not get PTZ configuration token")
                return False
                
            ptz_configuration_options = ptz_service.GetConfigurationOptions(request)
            
            # Create continuous move request
            request = ptz_service.create_type('ContinuousMove')
            request.ProfileToken = ptz_service.GetStatus().PTZConfiguration.token if ptz_service.GetStatus() else None
            
            if not request.ProfileToken:
                # Try to get from profiles
                media_service = camera.create_media_service()
                profiles = media_service.GetProfiles()
                for profile in profiles:
                    if hasattr(profile, 'PTZConfiguration') and profile.PTZConfiguration:
                        request.ProfileToken = profile.PTZConfiguration.token
                        break
            
            if not request.ProfileToken:
                logger.warning("Could not get PTZ profile token")
                return False
                
            # Set PTZ speeds
            if pan_tilt is not None or zoom is not None:
                if request.Velocity is None:
                    request.Velocity = {}
                if 'PanTilt' not in request.Velocity:
                    request.Velocity['PanTilt'] = {}
                if 'Zoom' not in request.Velocity:
                    request.Velocity['Zoom'] = {}
                    
                if pan_tilt is not None:
                    request.Velocity['PanTilt']['x'] = float(pan_tilt.get('x', 0))
                    request.Velocity['PanTilt']['y'] = float(pan_tilt.get('y', 0))
                    
                if zoom is not None:
                    request.Velocity['Zoom']['x'] = float(zoom.get('x', 0))
            
            # Apply speed if provided
            if speed is not None:
                if 'PanTilt' in request.Velocity and 'pan_tilt' in speed:
                    request.Velocity['PanTilt']['x'] *= float(speed['pan_tilt'])
                    request.Velocity['PanTilt']['y'] *= float(speed['pan_tilt'])
                if 'Zoom' in request.Velocity and 'zoom' in speed:
                    request.Velocity['Zoom']['x'] *= float(speed['zoom'])
            
            # Send the PTZ command
            ptz_service.ContinuousMove(request)
            logger.info(f"PTZ move command sent to {camera_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending PTZ command to {camera_key}: {e}")
            return False
    
    def ptz_stop(self, camera_key: str) -> bool:
        """
        Stop PTZ movement on an ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            
        Returns:
            True if command sent successfully, False otherwise
        """
        if not ONVIF_AVAILABLE or camera_key not in self.cameras:
            logger.error("ONVIF not available or camera not connected")
            return False
            
        try:
            camera = self.cameras[camera_key]
            ptz_service = camera.create_ptz_service()
            
            # Get profile token
            request = ptz_service.create_type('Stop')
            request.ProfileToken = ptz_service.GetStatus().PTZConfiguration.token if ptz_service.GetStatus() else None
            
            if not request.ProfileToken:
                # Try to get from profiles
                media_service = camera.create_media_service()
                profiles = media_service.GetProfiles()
                for profile in profiles:
                    if hasattr(profile, 'PTZConfiguration') and profile.PTZConfiguration:
                        request.ProfileToken = profile.PTZConfiguration.token
                        break
            
            if not request.ProfileToken:
                logger.warning("Could not get PTZ profile token for stop")
                return False
                
            # Send stop command
            ptz_service.Stop(request)
            logger.info(f"PTZ stop command sent to {camera_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending PTZ stop command to {camera_key}: {e}")
            return False
    
    def disconnect_device(self, camera_key: str) -> bool:
        """
        Disconnect from an ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            
        Returns:
            True if disconnection successful, False otherwise
        """
        try:
            if camera_key in self.cameras:
                del self.cameras[camera_key]
            if camera_key in self.devices:
                del self.devices[camera_key]
            logger.info(f"Disconnected from ONVIF device: {camera_key}")
            return True
        except Exception as e:
            logger.error(f"Error disconnecting from {camera_key}: {e}")
            return False
    
    def get_device_info(self, camera_key: str) -> Optional[ONVIFDeviceInfo]:
        """
        Get information about a connected ONVIF device.
        
        Args:
            camera_key: Key identifying the camera (hostname:port)
            
        Returns:
            ONVIFDeviceInfo object if found, None otherwise
        """
        return self.devices.get(camera_key)
    
    def list_devices(self) -> List[ONVIFDeviceInfo]:
        """
        List all connected ONVIF devices.
        
        Returns:
            List of ONVIFDeviceInfo objects
        """
        return list(self.devices.values())
    
    def start_discovery(self, timeout: int = 10):
        """
        Start automatic device discovery in background thread.
        
        Args:
            timeout: Discovery timeout in seconds
        """
        if self.discovery_active:
            logger.warning("Discovery already active")
            return
            
        self.discovery_active = True
        self.discovery_thread = threading.Thread(
            target=self._discovery_worker,
            args=(timeout,),
            daemon=True
        )
        self.discovery_thread.start()
        logger.info("Started ONVIF device discovery")
    
    def stop_discovery(self):
        """Stop automatic device discovery."""
        self.discovery_active = False
        if self.discovery_thread:
            self.discovery_thread.join(timeout=5)
        logger.info("Stopped ONVIF device discovery")
    
    def _discovery_worker(self, timeout: int):
        """Background worker for device discovery."""
        while self.discovery_active:
            try:
                devices = self.discover_devices(timeout)
                for device in devices:
                    device_key = f"{device.ip_address}:{device.port}"
                    if device_key not in self.devices:
                        logger.info(f"Discovered new ONVIF device: {device.name}")
                        # Attempt to connect automatically
                        self.connect_device(
                            device.ip_address,
                            device.port,
                            device.username,
                            device.password
                        )
                time.sleep(30)  # Discovery interval
            except Exception as e:
                logger.error(f"Error in discovery worker: {e}")
                time.sleep(5)


# Global ONVIF manager instance
onvif_manager = ONVIFManager()


def is_onvif_available() -> bool:
    """Check if ONVIF libraries are available."""
    return ONVIF_AVAILABLE


def get_onvif_manager() -> ONVIFManager:
    """Get the global ONVIF manager instance."""
    return onvif_manager