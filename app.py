from flask import render_template
from flaskapp import app


#loading the login page=================================================================================================================================
@app.route('/', methods = ['POST', 'GET'])
def login():
    return render_template('login.html',

    )


#initial=============================================================================================================================================
if __name__ == '__main__':
    app.run(port=5000, debug=False, ssl_context='adhoc')