#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import urllib, urllib2
import json
import random
import geoalchemy2 as geo
from shapely.geometry import asShape
from geoalchemy2.shape import from_shape
import geojson
from flask import Flask
from flask.ext.sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask.ext.script import Manager, Server
from flask.ext.migrate import Migrate, MigrateCommand
from flask.ext.assets import Environment, Bundle
from flask import render_template
from forms import SearchForm
from config import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = config.DATABASE_URL

assets = Environment(app)

sass_cpd = Bundle('sass/*.scss',
            filters=('scss') ,depends=('sass/*/*.scss'),
            output='css/global.css')

# minify css
cssmin_iwss = Bundle('css/*', sass_cpd,
            filters='cssmin', output='css/global.min.css')

# register assets
assets.register('css_all', cssmin_iwss)


db = SQLAlchemy(app)
migrate = Migrate(app, db)

manager = Manager(app)
manager.add_command('db', MigrateCommand)

# custom jinja line delimeters
app.jinja_env.line_statement_prefix = '%'
app.jinja_env.line_comment_prefix = '##'

class Zone(db.Model):
    __tablename__ = 'zones'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Unicode(length=200), nullable=False)
    regions = db.relationship("Region")


    def __init__(self, name):
        self.name = name

class Region(db.Model):
    __tablename__ = 'regions'
    id = db.Column(db.Integer, primary_key=True)
    geog = db.Column(geo.Geography(geometry_type='POLYGON', srid='4326'))
    zone_id = db.Column(db.Integer, db.ForeignKey('zones.id'))
    zone = db.relationship("Zone")

    def __init__(self, geog, zone_id):
        self.geog = geog
        self.zone_id = zone_id

class Officer(db.Model):
    __tablename__ = 'officers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Unicode(length=200), nullable=False)
    email = db.Column(db.Unicode(length=200), nullable=False)
    phone = db.Column(db.Unicode(length=200), nullable=False)
    title = db.Column(db.Unicode(length=200), nullable=False)

    def __init__(self, name, email, phone, title):
        self.name = name
        self.email = email
        self.phone = phone
        self.title = title

    def __repr__(self):
        return self.name

class ZoneAssignment(db.Model):
    __tablename__ = 'zone_assignments'
    id = db.Column(db.Integer, primary_key=True)
    zone_id = db.Column(db.Integer, db.ForeignKey('zones.id'))
    officer_id = db.Column(db.Integer, db.ForeignKey('officers.id'))

    zone = db.relationship(Zone, backref="zone_assignments")
    officer = db.relationship(Officer, backref="zone_assignments")

    def __init__(self, zone_id, officer_id):
        self.zone_id = zone_id
        self.officer_id = officer_id

def find_in_zone(lat, lon):
    result = Region.query.filter(
        Region.geog.ST_Covers(
            func.ST_GeomFromText(
                func.Concat('POINT(', lon, ' ', lat, ')'), 4326))).first()
    if result == None:
        return None
    else:
        return result.zone_id

def decode_address_to_coordinates(address):
    params = {
            'address' : address,
            'sensor' : 'false',
    }
    url = 'http://maps.google.com/maps/api/geocode/json?' + urllib.urlencode(params)
    response = urllib2.urlopen(url)
    result = json.load(response)
    try:
        return result['results'][0]['geometry']['location']
    except:
        return None

@manager.command
def reset_data():
    app_path = os.path.realpath(__file__)
    geojson_path = os.path.dirname(app_path) + "/../CPDZones.geojson"

    print "Deleting data if it exists."

    ZoneAssignment.query.delete()
    Officer.query.delete()
    Region.query.delete()
    Zone.query.delete()

    print "Loading data"

    fjson = geojson.load(open(geojson_path))
    for i in fjson.features:
        captain = Officer(i.properties['CAPT'], i.properties['CAPT_EMAIL'], i.properties['CAPT_PHONE'], u'Captain')
        lt = Officer(i.properties['LT'], i.properties['LT_EMAIL'], i.properties['LT_PHONE'], u'Lieutenant')
        zone = Zone(i.properties['CPD_Zone'])

        db.session.add(zone)

        capq = db.session.query(Officer).filter(Officer.name == i.properties['CAPT'])
        ltq = db.session.query(Officer).filter(Officer.name == i.properties['LT'])

        # Create Captain and/or Lieutenant if they don't exist.
        if not db.session.query(capq.exists()).scalar():
            db.session.add(captain)
        else:
            captain = Officer.query.filter_by(name=i.properties['CAPT']).first()

        if not db.session.query(ltq.exists()).scalar():
            db.session.add(lt)
        else:
            lt = Officer.query.filter_by(name=i.properties['LT']).first()

        db.session.commit()

        capt_assignment = ZoneAssignment(zone.id, captain.id)
        lt_assignment = ZoneAssignment(zone.id, lt.id)

        db.session.add(capt_assignment)
        db.session.add(lt_assignment)

        db.session.commit()

        if i.geometry.type == "MultiPolygon":
            polys = []
            for k in i.geometry.coordinates:
                kpolygon = geojson.Polygon(k)
                polys.append(Region(from_shape(asShape(kpolygon)), zone.id))

            db.session.add_all(polys)
            db.session.commit()
        elif i.geometry.type == "Polygon":
            poly = Region(from_shape(asShape(i.geometry)), zone.id)
            db.session.add(poly)
            db.session.commit()
        else:
            # Can't really handle it..
            pass

    print "Done"

@app.route("/", methods=['GET', 'POST'])
def index():
    form = SearchForm()
    cordinates = None
    if form.validate_on_submit():
        query = form.query.data
        cordinates = decode_address_to_coordinates(query)
        zone_id = find_in_zone(cordinates['lat'], cordinates['lng'])
        zone_info = ZoneAssignment.query.filter_by(zone_id=zone_id).all()
        return render_template('index.html', form=form, zone_info=zone_info, cordinates=cordinates)

    return render_template('index.html', form=form)

port = int(os.environ.get("PORT", 5000))
manager.add_command("runserver", Server(
    use_debugger=config.DEBUG,
    use_reloader=config.RELOAD,
    host='0.0.0.0',
    port=port)
)

if __name__ == "__main__":
    manager.run()
