import random
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import pypyodbc as odbc
from flask import request
from flask import redirect
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from datetime import datetime

# Dear Grader, we have provided the connection string separately as to not expose it publicly.
connection_string = '<insert connection string here>'
conn = odbc.connect(connection_string)

app = Flask(__name__)
app.secret_key = 'secret_key'

@app.route('/')
def login():
    return render_template('login.html')

@app.post('/login') 
def validate():
    ssn = request.form['ssn']
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Customer WHERE CustomerSSN = ?', [ssn])  # Use parameterized query to prevent SQL injection
    
    customer = cursor.fetchone()
    if not customer:
        flash('Invalid SSN.', 'error')
        return redirect(url_for('login'))


    # customer basic info
    # SSN, lastname, firstname, dob, gender, email, phone, lisc#
    customer_info = {
        'SSN': customer[0],
        'FirstName': customer[1],
        'LastName': customer[2], 
        'DOB': customer[3],
        'Gender': customer[4],
        'Email': customer[5],
        'Phone': customer[6],
        'LicenseNumber': customer[7],
    }
    print('customer info:', customer_info)
    session['customer_info'] = customer_info

    # customer driving profile, used in learning algorithm to predict premium
    cursor.execute('SELECT * FROM Driving_History WHERE CustomerSSN = ?', [ssn])
    driving_history = cursor.fetchone()

    cursor.execute('SELECT * FROM Vehicle WHERE CustomerSSN = ?', [ssn])
    vehicle = cursor.fetchone()

    cursor.execute('SELECT * FROM Address WHERE CustomerSSN = ?', [ssn])
    address = cursor.fetchone()

    print("driving history: ", driving_history)
    print("vehicle info: ", vehicle)


    # Calculate age based on Date of B  irth
    dob = customer[3]
    current_date = datetime.now()
    driver_age = current_date.year - dob.year - ((current_date.month, current_date.day) < (dob.month, dob.day))
    driving_profile = {
        'age': driver_age,
        'gender': customer[4],
        'mileage': vehicle[5],
        'trafficviolations': driving_history[1],
        'accidents': driving_history[2],
        'drivingexperience': driving_history[3]
    }

     # get address from ADDRESS table, addressline1, zip, custssn, agentssn, addressline2, state, city
    address_info = {
        'AddressLine1': address[0],
        'Zip': address[1],
        'AddressLine2': address[4],
        'State': address[5],
        'City': address[6]
    }

    vehicle_info = {
        'VIN': vehicle[0],
        'Brand': vehicle[1],
        'Model': vehicle[2],
        'Year': vehicle[3],
        'LicensePlate': vehicle[4],
        'Mileage': vehicle[5],
        'VehicleType': vehicle[6],
    }
    # select largest policy in database
    cursor.execute('SELECT * FROM Contract WHERE CustomerSSN = ? ORDER BY MonthlyPrice DESC', [ssn])
    policy = cursor.fetchone()

    # contractId, coverageType, maxCoverage, startDate, endDate, monthlyPremium
    policy_info = {
        'ContractID': policy[0],
        'CoverageType': policy[1],
        'MaxCoverage': policy[2],
        'MonthlyPremium': policy[5]
    }
    
    cursor.execute('SELECT * FROM Company WHERE CompanyCode = ?', [policy[6]])
    company = cursor.fetchone() 
    company_info = {
        'CompanyCode': company[0],
        'CompanyName': company[1],
    }

    session['company_info'] = company_info

    session['policy_info'] = policy_info
    session['vehicle_info'] = vehicle_info
    session['driving_profile'] = driving_profile
    session['address_info'] = address_info
    session['policy_info'] = policy_info
    print("driving profile:" , driving_profile)
    return redirect(url_for('home'))

@app.route('/home')
def home():
    customer_info = session.get('customer_info', None)
    print('Customer info:', customer_info)
    return render_template('home.html', user=customer_info)

@app.route('/profile')
def profile():
    customer_info = session.get('customer_info', None)
    vehicle_info = session.get('vehicle_info', None)
    driver_profile = session.get('driving_profile', None)
    address_info = session.get('address_info', None)
    policy_info = session.get('policy_info', None)
    return render_template('profile.html', policy=policy_info, user=customer_info, vehicle=vehicle_info, profile=driver_profile, address=address_info)

@app.route('/file-claim')
def file_claim():

    return render_template('file_claim.html')

@app.post('/file-claim')
def file_claim_post():
    # get data from post request in json form
    data = request.get_json()
    print(data)
    accident_date = data.get('accidentDate')
    accident_desc = data.get('accidentDesc')
    claim_amount = data.get('claimAmount')

    # convert accident_date_str to datetime object
    #accident_date = datetime.strptime(accident_date_str, '%Y-%m-%d').date()

    # generate random int for claim_id
    claim_id = random.randint(300, 10000)
    status = 'Pending'
    ssn = session.get('customer_info', None)['SSN']
    contract_id = session.get('policy_info', None)['ContractID']

    new_claim = [claim_id, accident_date, accident_desc, claim_amount, status, ssn, contract_id]
    print("Claim Info: ", new_claim)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO Claim (ClaimID, AccidentDate, AccidentDesc, ClaimAmount, Status, CustomerSSN, ContractID) VALUES (?, ?, ?, ?, ?, ?, ?)', new_claim)
    conn.commit()
    
    return jsonify({'message': 'Claim filed successfully!'})

@app.route('/generate-quote')
def generate_quote():
    driving_profile = session.get('driving_profile', None)
    premium_pred = round(predict_premium(quote_calculation_model, driving_profile)[0], 2)
    company_info = session.get('company_info', None)
    return render_template('generate_quote.html', quote=premium_pred, company=company_info)

# machine learning algorithm to predict premium
def learning_model():
    sql_query = "SELECT * FROM Profile"
    # Close the connection
    df_profile = pd.read_sql_query(sql_query, conn)
    print(df_profile)
    # Convert DOB to Age
    df_profile['dob'] = pd.to_datetime(df_profile['dob'])
    df_profile['age'] = df_profile['dob'].apply(lambda x: datetime.now().year - x.year)

    # Selecting relevant features
    features = ['age', 'customergender', 'mileage', 'trafficviolations', 'accidents', 'drivingexperience']
    X = df_profile[features]
    y = df_profile['monthlypremium']

    # Preprocessing
    numeric_features = ['age', 'mileage', 'trafficviolations', 'accidents', 'drivingexperience']
    categorical_features = ['customergender']

    preprocessor = ColumnTransformer(
        transformers=[
            ('num', StandardScaler(), numeric_features),
            ('cat', OneHotEncoder(), categorical_features)])

    # Define the model with XGBoost regressor
    model = Pipeline(steps=[('preprocessor', preprocessor),
                            ('regressor', xgb.XGBRegressor(objective='reg:squarederror', random_state=42))])

    # Split the data
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # Hyperparameter grid
    param_grid = {
        'regressor__n_estimators': [100, 200],
        'regressor__max_depth': [3, 5, 7],
        'regressor__learning_rate': [0.01, 0.1]
    }

    # Create a GridSearchCV object
    grid_search = GridSearchCV(model, param_grid, cv=5, scoring='neg_mean_squared_error')

    # Fit the model using grid search
    grid_search.fit(X_train, y_train)

    # Best parameters
    print("Best Parameters:", grid_search.best_params_)

    # Use the best model for predictions
    best_model = grid_search.best_estimator_

    # Predict and evaluate
    y_pred = best_model.predict(X_test)
    mse = mean_squared_error(y_test, y_pred)
    print(f"Mean Squared Error with Best Model: {mse}")

    # Print actual vs predicted for the first 10 instances
    for actual, predicted in zip(y_test[:10], y_pred[:10]):
        print(f"Actual: {actual}, Predicted: {predicted}")

    # After training your model, use this function to make predictions
    return best_model

# Function to predict premium based on the best model chosen by machine learning algorithm
def predict_premium(model, driving_profile):

    # Create DataFrame from user input
    data = pd.DataFrame({
        'age': [driving_profile['age']],
        'customergender': [driving_profile['gender']],
        'mileage': [driving_profile['mileage']],
        'trafficviolations': [driving_profile['trafficviolations']],
        'accidents': [driving_profile['accidents']],
        'drivingexperience': [driving_profile['drivingexperience']]
    })

    # Make a prediction using the best model
    premium_pred = model.predict(data)
    print(f"Predicted Monthly Premium for age {driving_profile['age']}: ${premium_pred[0]:.2f}")
    return premium_pred

if __name__ == '__main__':
    quote_calculation_model = learning_model()
    app.run(debug=True, port=3000)
    
    