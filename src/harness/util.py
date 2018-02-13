#    Copyright 2016 SAS Project Authors. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
"""Helper functions for test harness."""

from collections import defaultdict
from datetime import datetime
from functools import wraps
import inspect
import json
import logging
import os
import random
import uuid
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import rsa

import jwt

from shapely.geometry import shape, Point, LineString


def _log_testcase_header(name, doc):
  logging.getLogger().setLevel(logging.INFO)
  logging.info('Running WinnForum test case %s:', name)
  logging.info(doc)


def winnforum_testcase(testcase):
  """Decorator for common features (e.g. logging) for WinnForum test cases."""
  def decorated_testcase(*args, **kwargs):
    assert testcase, ('Avoid using @winnforum_testcase with '
                      '@configurable_testcase')

    _log_testcase_header(testcase.__name__, testcase.__doc__)
    testcase(*args, **kwargs)

  return decorated_testcase


def configurable_testcase(default_config_function):
  """Decorator to make a test case configurable."""

  def internal_configurable_testcase(testcase):

    def wrapper_function(func, name, config, generate_default_func):
      @wraps(func)
      def _func(*a):
        if generate_default_func:
          generate_default_func(*a)
        _log_testcase_header(name, func.func_doc)
        return func(*a, config_filename=config)
      _func.__name__ = name
      return _func

    def generate_default(func, default_filename):
      @wraps(func)
      def _func(*a):
        return func(*a, filename=default_filename)
      return _func

    # Create config directory for this function if it doesn't already exist.
    harness_dir = os.path.dirname(
              os.path.abspath(inspect.getfile(inspect.currentframe())))
    config_dir = os.path.join(harness_dir, 'testcases', 'configs', testcase.func_name)
    config_names = os.listdir(config_dir) if os.path.exists(config_dir) else []

    # No existing configs => generate default config.
    generate_default_func = None
    if not config_names:
      default_config_filename = os.path.join(config_dir, 'default.config')
      logging.info("%s: Creating default config at '%s'", testcase.func_name,
                   default_config_filename)
      generate_default_func = generate_default(default_config_function,
                                               default_config_filename)
      config_names.append('default.config')

    # Run once for each config.
    stack = inspect.stack()
    frame = stack[1]  # Picks the 'testcase' frame.
    frame_locals = frame[0].f_locals
    for i, config_name in enumerate(config_names):
      base_config_name = os.path.splitext(config_name)[0]
      name = '%s_%d_%s' % (testcase.func_name, i, base_config_name)
      config_filename = os.path.join(config_dir, config_name)
      frame_locals[name] = wrapper_function(testcase, name, config_filename,
                                            generate_default_func)

  return internal_configurable_testcase


def loadConfig(config_filename):
  """Loads a configuration file."""
  with open(config_filename, 'r') as f:
    return json.loads(f.read())


def writeConfig(config_filename, config):
  """Writes a configuration file."""
  dir_name = os.path.dirname(config_filename)
  if not os.path.exists(dir_name):
    os.makedirs(dir_name)

  with open(config_filename, 'w') as f:
    f.write(
        json.dumps(config, indent=2, sort_keys=False, separators=(',', ': ')))


def getRandomLatLongInPolygon(ppa):
  """Generate the Random Latitude and Longitude inside the PPA Polygon
  Args:
    ppa: (dictionary) A dictionary containing PPA Record.
  Returns:
    A tuple in the form of latitude and longitude which itself is
    a number.
  """

  try:
    ppa_polygon = shape(ppa['zone']['features'][0]['geometry'])
    min_lng, min_lat, max_lng, max_lat = ppa_polygon.bounds
    lng = random.uniform(min_lng, max_lng)
    lng_line = LineString([(lng, min_lat), (lng, max_lat)])
    lng_line_intercept_min, lng_line_intercept_max = \
      lng_line.intersection(ppa_polygon).xy[1].tolist()
    lat = random.uniform(lng_line_intercept_min, lng_line_intercept_max)
    if Point([lng, lat]).within(ppa_polygon):
      return lat, lng
    else:
      return getRandomLatLongInPolygon(ppa)
  except NotImplementedError:
    # Cannot get the intercept call it again
    return getRandomLatLongInPolygon(ppa)


def makePpaAndPalRecordsConsistent(ppa_record, pal_records, low_frequency,
                                   high_frequency, user_id, fcc_channel_id="1"):
  """Make PPA and PAL object consistent with the inputs

    Args:
      ppa_record: (dictionary) A dictionary containing PPA Record.
      pal_records: (list) A list of PAL Records in the form of dictionary
      which has to be associated with the PPA.
      low_frequency: (number) The Primary Low Frequency for PAL.
      high_frequency: (number) The Primary High Frequency for PAL.
      user_id: (string) The userId from the CBSD.
      fcc_channel_id: (string) The FCC-supplied frequency channel identifier.

    Returns:
      A tuple containing a ppa record which itself is a dictionary and pal records 
      list which contains individual pal records in the form of dictionary.
    Note: The PAL Dictionary must contain censusYear(number) and 
          fipsCode(number)
  """

  previous_year_date = datetime.now().replace(year=datetime.now().year - 1)
  next_year_date = datetime.now().replace(year=datetime.now().year + 1)

  for index, pal_rec in enumerate(pal_records):
    pal_fips_code = pal_rec['fipsCode']
    pal_census_year = pal_rec['censusYear']
    del pal_rec['fipsCode'], pal_rec['censusYear']

    pal_rec = defaultdict(lambda: defaultdict(dict), pal_rec)
    # Change the FIPS Code and Registration Date-Year in Pal Id
    pal_rec['palId'] = '/'.join(['pal', '%s-%d' %
                                 ('{:02d}'.format(previous_year_date.month),
                                  previous_year_date.year),
                                 str(pal_fips_code), fcc_channel_id])
    pal_rec['userId'] = user_id
    # Make the date consistent in Pal Record for Registration and License
    pal_rec['registrationInformation']['registrationDate'] = \
      previous_year_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Change License Information in Pal
    pal_rec['license']['licenseAreaIdentifier'] = str(pal_fips_code)
    pal_rec['license']['licenseAreaExtent'] = \
      'zone/census_tract/census/%d/%d' % (pal_census_year, pal_fips_code)
    pal_rec['license']['licenseDate'] = previous_year_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    pal_rec['license']['licenseExpiration'] = next_year_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    pal_rec['license']['licenseFrequencyChannelId'] = fcc_channel_id
    # Change Frequency Information in Pal
    pal_rec['channelAssignment']['primaryAssignment']['lowFrequency'] = low_frequency
    pal_rec['channelAssignment']['primaryAssignment']['highFrequency'] = high_frequency
    # Converting from defaultdict to dict
    pal_records[index] = json.loads(json.dumps(pal_rec))

  # Add Pal Ids into the Ppa Record
  ppa_record = defaultdict(lambda: defaultdict(dict), ppa_record)

  ppa_record['ppaInfo']['palId'] = [pal['palId'] for pal in pal_records]
  ppa_record['id'] = 'zone/ppa/%s/%s/%s' % (ppa_record['creator'],
                                            ppa_record['ppaInfo']['palId'][0],
                                            uuid.uuid4().hex)

  # Make the date consistent in Ppa Record
  ppa_record['ppaInfo']['ppaBeginDate'] = previous_year_date.strftime('%Y-%m-%dT%H:%M:%SZ')
  ppa_record['ppaInfo']['ppaExpirationDate'] = next_year_date.strftime('%Y-%m-%dT%H:%M:%SZ')
  # Converting from defaultdict to dict
  ppa_record = json.loads(json.dumps(ppa_record))
  return ppa_record, pal_records


def generateCpiRsaKeys():
  """Generate a private/public RSA 2048 key pair.

  Returns:
    A tuple (private_key, public key) as PEM string encoded.
  """
  rsa_key = rsa.generate_private_key(
      public_exponent=65537, key_size=2048, backend=default_backend())
  rsa_private_key = rsa_key.private_bytes(
      encoding=serialization.Encoding.PEM,
      format=serialization.PrivateFormat.TraditionalOpenSSL,
      encryption_algorithm=serialization.NoEncryption())
  rsa_public_key = rsa_key.public_key().public_bytes(
      encoding=serialization.Encoding.PEM,
      format=serialization.PublicFormat.SubjectPublicKeyInfo)
  return rsa_private_key, rsa_public_key


def generateCpiEcKeys():
  """Generate a private/public EC SECP256R1 key pair.

  Returns:
    A tuple (private_key, public key) as PEM string encoded.
  """
  ec_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
  ec_private_key = ec_key.private_bytes(
      encoding=serialization.Encoding.PEM,
      format=serialization.PrivateFormat.TraditionalOpenSSL,
      encryption_algorithm=serialization.NoEncryption())
  ec_public_key = ec_key.public_key().public_bytes(
      encoding=serialization.Encoding.PEM,
      format=serialization.PublicFormat.SubjectPublicKeyInfo)
  return ec_private_key, ec_public_key


def convertRequestToRequestWithCpiSignature(private_key, cpi_id,
                                            cpi_name, request,
                                            jwt_algorithm='RS256'):
  """Converts a regular registration request to contain cpiSignatureData
     using the given JWT signature algorithm.

  Args:
    private_key: (string) valid PEM encoded string.
    cpi_id: (string) valid cpiId.
    cpi_name: (string) valid cpiName.
    request: individual CBSD registration request (which is a dictionary).
    jwt_algorithm: (string) algorithm to sign the JWT, defaults to 'RS256'.
  """
  cpi_signed_data = {}
  cpi_signed_data['fccId'] = request['fccId']
  cpi_signed_data['cbsdSerialNumber'] = request['cbsdSerialNumber']
  cpi_signed_data['installationParam'] = request['installationParam']
  del request['installationParam']
  cpi_signed_data['professionalInstallerData'] = {}
  if cpi_id:
    cpi_signed_data['professionalInstallerData']['cpiId'] = cpi_id
  cpi_signed_data['professionalInstallerData']['cpiName'] = cpi_name
  cpi_signed_data['professionalInstallerData'][
      'installCertificationTime'] = datetime.utcnow().strftime(
          '%Y-%m-%dT%H:%M:%SZ')
  compact_jwt_message = jwt.encode(
      cpi_signed_data, private_key, jwt_algorithm)
  jwt_message = compact_jwt_message.split('.')
  request['cpiSignatureData'] = {}
  request['cpiSignatureData']['protectedHeader'] = jwt_message[0]
  request['cpiSignatureData']['encodedCpiSignedData'] = jwt_message[1]
  request['cpiSignatureData']['digitalSignature'] = jwt_message[2]

def getPPinPPA(boundarysPoints, ARCSEC):
    import numpy as np
    from shapely import geometry
    
    poly = geometry.Polygon([[lon,lat] for lon,lat in boundarysPoints])
    arcsec = ARCSEC/3600.0
    maxlat = -90 
    minlat = 90
    maxlon = -180
    minlon = 180
       
    for point in boundarysPoints:
        if point[1] > maxlat:
            maxlat = point[1]
        if point[1] < minlat:
            minlat = point[1]         
        if point[0] > maxlon:
            maxlon = point[0]
        if point[0] < minlon:
            minlon = point[0]
    
    iLat = np.floor(minlat)
    iLon = np.floor(minlon)
    nL = np.floor((minlat - iLat)/arcsec)
    nH = np.ceil((maxlat - iLat)/arcsec)
    mL = np.floor((minlon - iLon)/arcsec)
    mH = np.ceil((maxlon - iLon)/arcsec)
        
    latgrid = np.arange(iLat + nL*arcsec, iLat + nH *arcsec, arcsec)
    longrid = np.arange(iLon + mL * arcsec, iLon + mH *arcsec, arcsec)
    
    if len(latgrid) > 0 and len(latgrid)>0:
        ppa = []

        for lat in latgrid:
            for lon in longrid:
                ppa_check_point = Point(lon, lat)
                if ppa_check_point.within(poly):
                        ppa.extend([[lon, lat]])

        if (len(ppa) == 1):
            return 0, ppa[0][0], ppa[0][1]
        else:
            return -1, -1, -1
    else:
        return -1, -1, -1

def QueryPropagationAntennaModel(requestJson):
    import os
    from reference_models.propagation import wf_itm
    from reference_models.propagation import wf_hybrid
    from reference_models.antenna import antenna
    from reference_models.geo import vincenty
    from reference_models.geo import nlcd
    TERRAIN_TEST_DIR = os.path.join(os.path.dirname(__file__), 'reference_models', 'geo', 'testdata', 'ned')
    NLCD_TEST_DIR = os.path.join(os.path.dirname(__file__), 'reference_models', 'geo', 'testdata', 'nlcd')
    ITU_TEST_DIR = os.path.join(os.path.dirname(__file__), 'reference_models', 'geo', 'testdata', 'itu')
    wf_itm.ConfigureTerrainDriver(terrain_dir=TERRAIN_TEST_DIR)
    wf_itm.ConfigureItuDrivers(itu_dir=ITU_TEST_DIR)
    wf_hybrid.ConfigureTerrainDriver(terrain_dir=TERRAIN_TEST_DIR)
    wf_hybrid.wf_itm.ConfigureItuDrivers(itu_dir=ITU_TEST_DIR)
    nlcd_driver = nlcd.NlcdDriver(NLCD_TEST_DIR)

    request = json.loads(requestJson)
    reliabilityLevel = request['reliabilityLevel']
    if reliabilityLevel not in [-1, 0.05, 0.95]:
        response = 404
        return response
    Tx = request['cbsd']
    if ('fss' in request) and ('ppa' in request):
        response = 404
        return response
    elif 'ppa' in request:
        Rx ={}
        Rx['height'] = 1.5
        isfss = False
        coordinates = [];
        ppa = request['ppa']
        boundarysPoints = ppa['geometry']['coordinates']
        boundarypoints = []
        for BP in boundarysPoints:
            boundarypoints.append([BP[1], BP[0]])
        
        regionVal = nlcd_driver.RegionNlcdVote(boundarypoints)
        ARCSEC = 2
        res, lat, lon = getPPinPPA(boundarysPoints, ARCSEC)
        if res== 0:
            Rx['latitude'] = lat
            Rx['longitude']= lon
        else:
            response = 404
            return response

    elif 'fss' in request:
        isfss = True
        Rx = request['fss']
    else:
        response = 404
        return response
    
# ITM pathloss (if receiver type is FSS) or the hybrid model pathloss (if receiver type is PPA) and corresponding antenna gains.
# The test specification notes that the SAS UUT shall use default values for w1 and w2 in the ITM model.
    Result = {}
    
    d, az, rev_az = vincenty.GeodesicDistanceBearing(Tx['latitude'], Tx['longitude'], Rx['latitude'], Rx['longitude'])

    gainTxRx = antenna.GetStandardAntennaGains(az, ant_azimuth=Tx['antennaAzimuth'], ant_beamwidth=Tx['antennaBeamwidth'], ant_gain=Tx['antennaGain'])
    Result['txAntennaGainDbi'] = gainTxRx   

    if isfss:
        PathLoss = wf_itm.CalcItmPropagationLoss(Tx['latitude'], Tx['longitude'], Tx['height'], Rx['latitude'], Rx['longitude'], Rx['height'], reliability=reliabilityLevel, freq_mhz=3625.)
        Result['pathlossDb'] = PathLoss.db_loss
        if 'rxAntennaGainRequired' in Rx.keys():
            hor_dirs = rev_az
            ver_dirs = PathLoss.incidence_angles.ver_rx
            gainRxTx = antenna.GetFssAntennaGains(hor_dirs, ver_dirs, Rx['antennaAzimuth'], Rx['antennaDowntilt'], Rx['antennaGain'])
            Result['rxAntennaGainDbi'] = gainRxTx        
    else:
        
        PathLoss = wf_hybrid.CalcHybridPropagationLoss(Tx['latitude'], Tx['longitude'], Tx['height'], Rx['latitude'], Rx['longitude'], Rx['height'], reliability=-1, freq_mhz=3625., region=regionVal)       
        Result['pathlossDb'] = PathLoss.db_loss

    return Result